"""Persistent Discord delivery, processed outside reservation HTTP requests."""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..auth import display_name
from ..models import DiscordDelivery, SessionRequest, UserProfile

logger = logging.getLogger(__name__)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discord_request(url, method, payload):
    request = Request(url, data=json.dumps(payload).encode(), method=method,
                      headers={"Content-Type": "application/json", "User-Agent": "3istor-Sessions/1.0"})
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        return json.load(response)


def message_payload(db, item, settings):
    labels = {"pending": ("⏳ En attente", 0xF59E0B), "approved": ("✅ Acceptée", 0x22C55E), "declined": ("❌ Refusée", 0xEF4444)}
    label, color = labels[item.status.value]
    zone = ZoneInfo("Europe/Paris")
    start, end = item.start_at.astimezone(zone), item.end_at.astimezone(zone)
    names = []
    for participant in item.participants:
        profile = db.get(UserProfile, participant.email)
        names.append(f"{profile.first_name} {profile.last_name}" if profile else display_name(participant.email))
    fields = [
        {"name": "Statut", "value": label},
        {"name": "Date et horaires · Europe/Paris", "value": f"{start:%d/%m/%Y} · {start:%H:%M}–{end:%H:%M}"},
        {"name": "Participants", "value": ", ".join(names)[:1024] or "—"},
        {"name": "Demandée par", "value": item.requester_name[:1024]},
    ]
    fields.append({"name": "Type de demande", "value": (
        f"Modification de la session #{item.modifies_request_id}" if item.modifies_request_id
        else "Demande de session"
    )})
    if item.previous_session:
        previous = item.previous_session
        old_start = datetime.fromisoformat(previous["start_at"]).astimezone(zone)
        old_end = datetime.fromisoformat(previous["end_at"]).astimezone(zone)
        fields.append({"name": "Avant modification", "value": f"{previous['title']}\n{old_start:%d/%m/%Y %H:%M}–{old_end:%H:%M}"[:1024]})
    if item.manager_note:
        fields.append({"name": "Note du manager", "value": item.manager_note[:1024]})
    if item.is_forced:
        fields.append({"name": "Planification", "value": "Créneau forcé par un manager"})
    return {"allowed_mentions": {"parse": []}, "embeds": [{
        "title": item.title[:256], "url": settings.frontend_url.rstrip("/"),
        "color": color, "fields": fields,
        "footer": {"text": f"3istor · Session #{item.id}"},
    }]}


def sync_delivery(db, delivery, settings):
    url = settings.discord_webhook_url.get_secret_value()
    if not url:
        return
    item = db.get(SessionRequest, delivery.request_id)
    if item is None:
        return
    webhook_id = url.split("/")[-2]
    payload = message_payload(db, item, settings)
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if delivery.delivered_hash == signature and delivery.webhook_id == webhook_id:
        return
    try:
        if delivery.message_id and delivery.webhook_id == webhook_id:
            result = discord_request(f"{url}/messages/{delivery.message_id}", "PATCH", payload)
        else:
            result = discord_request(f"{url}?wait=true", "POST", payload)
        message_id = str(result["id"])
        if not message_id.isdigit():
            raise ValueError("Invalid Discord message ID")
        delivery.message_id = message_id
        delivery.webhook_id = webhook_id
        delivery.delivered_hash = signature
        delivery.failures = 0
        delivery.retry_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        delay = min(300, 5 * 2 ** min(delivery.failures, 6))
        if isinstance(exc, HTTPError):
            try:
                body = json.loads(exc.read())
                if exc.code == 429:
                    delay = max(delay, float(body.get("retry_after", delay)))
                elif exc.code == 404 and body.get("code") == 10008:
                    delivery.message_id = None
                    delivery.delivered_hash = ""
            except (ValueError, TypeError):
                pass
        delivery.failures += 1
        delivery.retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        db.commit()
        # Never log the exception text: HTTP errors can contain the secret URL.
        logger.warning("Discord delivery failed for session %s (%s); retry scheduled", delivery.request_id, type(exc).__name__)


def run_worker(stop, session_factory, settings):
    if not settings.discord_webhook_url.get_secret_value():
        return
    while not stop.is_set():
        try:
            with session_factory() as db:
                ids = list(db.scalars(select(DiscordDelivery.request_id).where(
                    DiscordDelivery.retry_at <= datetime.now(timezone.utc))))
            for request_id in ids:
                if stop.is_set():
                    return
                with session_factory() as db:
                    sync_delivery(db, db.get(DiscordDelivery, request_id), settings)
        except Exception as exc:
            logger.warning("Discord worker failed (%s); will retry", type(exc).__name__)
        stop.wait(5)
