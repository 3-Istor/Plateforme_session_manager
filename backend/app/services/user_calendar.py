from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import CalendarConnection, ManagedCalendar, SelectedCalendar
from .availability import BusyPeriods

FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
ACCESSIBLE_FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
SELECTION_SCOPES = {CALENDAR_LIST_SCOPE, ACCESSIBLE_FREEBUSY_SCOPE}


class CalendarAuthorizationRequired(ValueError):
    pass


# Kept for compatibility with connections made by older releases. New manager
# connections use SHARED_EVENTS_SCOPE for the configured existing team calendar.
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
SHARED_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
STATE_TTL_SECONDS = 10 * 60
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]

# Google can return canonical aliases for identity scopes (for example,
# userinfo.email instead of "email"). OAuthlib treats aliases as a scope
# change, so we relax that syntactic check and enforce Calendar scopes below.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def manager_events_scope(settings: Settings) -> str:
    # Sessions are always written to the configured, existing team calendar.
    return SHARED_EVENTS_SCOPE


def required_scopes(is_manager: bool, events_scope: str = SHARED_EVENTS_SCOPE) -> list[str]:
    scopes = [FREEBUSY_SCOPE]
    if is_manager:
        scopes.append(events_scope)
    return scopes


def authorization_scopes(settings: Settings, is_manager: bool, manage_calendars: bool = False) -> list[str]:
    return [*IDENTITY_SCOPES, *required_scopes(is_manager, manager_events_scope(settings)),
            *sorted(SELECTION_SCOPES if manage_calendars else [])]


def _client_config(settings: Settings) -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def _fernet(settings: Settings) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest())
    return Fernet(key)


def encrypt_refresh_token(settings: Settings, token: str) -> str:
    return _fernet(settings).encrypt(token.encode()).decode()


def decrypt_refresh_token(settings: Settings, token: str) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Le jeton Calendar ne peut pas être déchiffré. Reconnectez l'agenda.") from exc


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_oauth_state(settings: Settings, email: str, is_manager: bool, manage_calendars: bool = False) -> str:
    payload = _b64encode(
        json.dumps(
            {"email": email.lower(), "manager": is_manager, "manage_calendars": manage_calendars, "exp": int(time.time()) + STATE_TTL_SECONDS,
             "nonce": secrets.token_urlsafe(32)},
            separators=(",", ":"),
        ).encode()
    )
    signature = _b64encode(hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_oauth_state(settings: Settings, state: str) -> dict:
    try:
        payload, signature = state.split(".", 1)
        expected = _b64encode(hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(_b64decode(payload))
        if int(data["exp"]) < int(time.time()):
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("État OAuth invalide ou expiré") from exc


def _pkce_verifier(settings: Settings, state: str) -> str:
    # Reconstruct the same secret proof on callback without putting it in the
    # public state or process memory. A random state nonce makes each flow unique.
    # Domain separation prevents reuse of the state-signature HMAC as a verifier.
    return _b64encode(hmac.new(
        settings.app_secret.encode(),
        b"google-calendar-pkce-v1\x00" + state.encode(),
        hashlib.sha256,
    ).digest())


def authorization_url(settings: Settings, email: str, is_manager: bool, manage_calendars: bool = False) -> str:
    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET sont obligatoires")
    if is_manager and not settings.google_target_calendar_id.strip():
        raise ValueError("GOOGLE_TARGET_CALENDAR_ID est obligatoire pour le manager")
    state = create_oauth_state(settings, email, is_manager, manage_calendars)
    flow = Flow.from_client_config(
        _client_config(settings), scopes=authorization_scopes(settings, is_manager, manage_calendars), state=state,
        code_verifier=_pkce_verifier(settings, state), autogenerate_code_verifier=False,
    )
    flow.redirect_uri = settings.google_redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        login_hint=email,
    )
    return url


def exchange_code(
    db: Session,
    settings: Settings,
    *,
    code: str,
    state: str,
) -> str:
    state_data = verify_oauth_state(settings, state)
    email = str(state_data["email"]).lower()
    is_manager = bool(state_data["manager"])
    manage_calendars = bool(state_data.get("manage_calendars", False))
    flow = Flow.from_client_config(
        _client_config(settings), scopes=authorization_scopes(settings, is_manager, manage_calendars), state=state,
        code_verifier=_pkce_verifier(settings, state), autogenerate_code_verifier=False,
    )
    flow.redirect_uri = settings.google_redirect_uri
    flow.fetch_token(code=code)
    credentials = flow.credentials
    raw_granted_scopes = credentials.granted_scopes or credentials.scopes or []
    granted_scopes = set(
        raw_granted_scopes.split() if isinstance(raw_granted_scopes, str) else raw_granted_scopes
    )
    missing_calendar_scopes = set(required_scopes(is_manager, manager_events_scope(settings))) - granted_scopes
    if manage_calendars:
        missing_calendar_scopes |= SELECTION_SCOPES - granted_scopes
    if missing_calendar_scopes:
        raise ValueError(
            "Permissions Calendar manquantes : " + ", ".join(sorted(missing_calendar_scopes))
        )
    if not credentials.id_token:
        raise ValueError("Google n'a pas confirmé l'identité du calendrier")
    identity = id_token.verify_oauth2_token(
        credentials.id_token,
        google_requests.Request(),
        settings.google_client_id,
    )
    authorized_email = str(identity.get("email", "")).lower()
    if not identity.get("email_verified") or authorized_email != email:
        raise ValueError("Connectez l'agenda appartenant au même compte Google")
    if is_manager:
        verify_target_calendar_write_access(credentials, settings)
    existing = db.get(CalendarConnection, email)
    refresh_token = credentials.refresh_token
    if not refresh_token and existing:
        refresh_token = decrypt_refresh_token(settings, existing.encrypted_refresh_token)
    if not refresh_token:
        raise ValueError("Google n'a pas fourni d'accès durable. Recommencez la connexion.")
    connection = existing or CalendarConnection(email=email)
    connection.encrypted_refresh_token = encrypt_refresh_token(settings, refresh_token)
    connection.scopes = " ".join(sorted(set(granted_scopes)))
    db.add(connection)
    if is_manager:
        target_calendar_id = settings.google_target_calendar_id.strip()
        # Keep this legacy table as a verification marker. Removing a stale
        # marker first also makes a manager change safe despite its historical
        # unique constraint on calendar_id.
        db.execute(
            delete(ManagedCalendar).where(
                ManagedCalendar.calendar_id == target_calendar_id,
                ManagedCalendar.email != email,
            )
        )
        verified_target = db.get(ManagedCalendar, email)
        if verified_target:
            verified_target.calendar_id = target_calendar_id
        else:
            db.add(ManagedCalendar(email=email, calendar_id=target_calendar_id))
    db.commit()
    return email


def verify_target_calendar_write_access(credentials: Credentials, settings: Settings) -> None:
    """Fail OAuth connection unless the manager can edit the exact target."""
    calendar_id = settings.google_target_calendar_id.strip()
    if not calendar_id:
        raise ValueError("GOOGLE_TARGET_CALENDAR_ID est obligatoire pour le manager")
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    result = service.events().list(
        calendarId=calendar_id,
        maxResults=1,
        fields="accessRole",
    ).execute()
    if result.get("accessRole") not in {"writer", "owner"}:
        raise ValueError(
            "Le compte manager doit avoir le droit de modifier les événements "
            "de l'agenda 3-ISTOR configuré"
        )


def connection_scopes(connection: CalendarConnection | None) -> set[str]:
    return set(connection.scopes.split()) if connection else set()


def has_required_connection(db: Session, settings: Settings, email: str, is_manager: bool) -> bool:
    scopes = connection_scopes(db.get(CalendarConnection, email.lower()))
    expected = required_scopes(is_manager, manager_events_scope(settings))
    if not set(expected).issubset(scopes):
        return False
    if not is_manager:
        return True
    verified_target = db.get(ManagedCalendar, email.lower())
    return bool(
        verified_target
        and settings.google_target_calendar_id.strip()
        and verified_target.calendar_id == settings.google_target_calendar_id.strip()
    )


def connected_emails(db: Session) -> list[str]:
    return list(db.scalars(select(CalendarConnection.email).order_by(CalendarConnection.email)).all())


def selected_calendars(db: Session | None, email: str) -> list[SelectedCalendar]:
    if db is None:
        return []
    return list(db.scalars(select(SelectedCalendar).where(SelectedCalendar.email == email.lower())
                           .order_by(SelectedCalendar.calendar_id)))


def has_selection_permissions(db: Session, email: str) -> bool:
    return SELECTION_SCOPES.issubset(connection_scopes(db.get(CalendarConnection, email.lower())))


def available_calendars(db: Session, settings: Settings, email: str) -> dict[str, dict]:
    if not has_selection_permissions(db, email):
        raise CalendarAuthorizationRequired("Autorisez Google à lister vos agendas et consulter leurs disponibilités.")
    credentials = _credentials(db, settings, email, CALENDAR_LIST_SCOPE)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    calendars = {"primary": {"id": "primary", "name": "Agenda principal", "primary": True}}
    token = None
    seen = set()
    while True:
        result = service.calendarList().list(
            maxResults=250, minAccessRole="freeBusyReader", showHidden=True, showDeleted=False,
            fields="nextPageToken,items(id,summary,primary,deleted)", **({"pageToken": token} if token else {}),
        ).execute()
        if not isinstance(result.get("items"), list):
            raise ValueError("Google n'a pas renvoyé la liste complète de vos agendas")
        for item in result["items"]:
            if item.get("deleted") or not item.get("id"):
                continue
            primary = bool(item.get("primary"))
            calendar_id = "primary" if primary else item["id"]
            if not primary and calendar_id in settings.availability_calendar_ids:
                continue  # Mandatory collective calendars are checked separately.
            calendars[calendar_id] = {"id": calendar_id, "name": str(item.get("summary") or item["id"])[:255], "primary": primary}
        token = result.get("nextPageToken")
        if not token:
            return calendars
        if token in seen or len(seen) >= 100:
            raise ValueError("Impossible de charger tous les agendas Google")
        seen.add(token)


def calendar_selection(db: Session, settings: Settings, email: str) -> dict:
    rows = selected_calendars(db, email)
    selected = {row.calendar_id for row in rows}
    connected = FREEBUSY_SCOPE in connection_scopes(db.get(CalendarConnection, email.lower()))
    permitted = connected and has_selection_permissions(db, email)
    choices = available_calendars(db, settings, email) if permitted else {
        "primary": {"id": "primary", "name": "Agenda principal", "primary": True},
    }
    options = [{**value, "selected": value["primary"] or key in selected, "available": True}
               for key, value in choices.items()]
    options.extend({"id": row.calendar_id, "name": row.name, "primary": False, "selected": True, "available": False}
                   for row in rows if row.calendar_id not in choices)
    return {"connected": connected, "authorization_required": not permitted, "calendars": options}


def save_calendar_selection(db: Session, settings: Settings, email: str, calendar_ids: list[str]) -> None:
    email = email.lower()
    if db.get(CalendarConnection, email) is None:
        raise CalendarAuthorizationRequired("Connectez vos agendas Google avant de modifier la sélection.")
    chosen = set(calendar_ids) - {"primary", email}
    if len(chosen) > 49:
        raise ValueError("Choisissez au maximum 49 agendas supplémentaires")
    if chosen & set(settings.availability_calendar_ids):
        raise ValueError("Les agendas collectifs sont toujours vérifiés et ne peuvent pas être modifiés ici")
    current = {row.calendar_id: row.name for row in selected_calendars(db, email)}
    if chosen.issubset(current):
        names = current  # Removing an inaccessible calendar never requires new consent.
    else:
        choices = available_calendars(db, settings, email)
        if not chosen.issubset(choices):
            raise ValueError("Un agenda sélectionné n'est pas accessible depuis votre compte Google")
        names = {key: value["name"] for key, value in choices.items()}
    db.execute(delete(SelectedCalendar).where(SelectedCalendar.email == email))
    for calendar_id in sorted(chosen):
        db.add(SelectedCalendar(email=email, calendar_id=calendar_id, name=names[calendar_id]))
    db.commit()


def _credentials(db: Session, settings: Settings, email: str, required_scope: str) -> Credentials:
    connection = db.get(CalendarConnection, email.lower())
    if not connection or required_scope not in connection_scopes(connection):
        raise ValueError(f"Agenda non connecté : {email}")
    credentials = Credentials(
        token=None,
        refresh_token=decrypt_refresh_token(settings, connection.encrypted_refresh_token),
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=list(connection_scopes(connection)),
    )
    credentials.refresh(Request())
    return credentials


def freebusy_for_members(
    db: Session,
    settings: Settings,
    emails: list[str],
    start_at: datetime,
    end_at: datetime,
) -> BusyPeriods:
    periods = BusyPeriods(by_participant={email.lower(): [] for email in emails})
    for email in emails:
        extras = selected_calendars(db, email)
        credentials = _credentials(db, settings, email, ACCESSIBLE_FREEBUSY_SCOPE if extras else FREEBUSY_SCOPE)
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        response = service.freebusy().query(
            body={
                "timeMin": start_at.isoformat(),
                "timeMax": end_at.isoformat(),
                "items": [{"id": calendar_id} for calendar_id in ["primary", *(row.calendar_id for row in extras)]],
            }
        ).execute()
        calendars = response.get("calendars", {})
        for calendar_id in ["primary", *(row.calendar_id for row in extras)]:
            calendar = calendars.get(calendar_id, {})
            if calendar.get("errors") or not isinstance(calendar.get("busy"), list):
                raise ValueError(f"Impossible de lire un agenda sélectionné de {email}. Vérifiez la sélection ou reconnectez Google.")
            for busy in calendar["busy"]:
                periods.by_participant[email.lower()].append(
                    (datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["end"]))
                )

    availability_calendar_ids = settings.availability_calendar_ids
    if availability_calendar_ids:
        manager_email = str(settings.manager_email).lower()
        credentials = _credentials(db, settings, manager_email, FREEBUSY_SCOPE)
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        response = service.freebusy().query(
            body={
                "timeMin": start_at.isoformat(),
                "timeMax": end_at.isoformat(),
                "items": [
                    {"id": calendar_id} for calendar_id in availability_calendar_ids
                ],
            }
        ).execute()
        calendars = response.get("calendars", {})
        for calendar_id in availability_calendar_ids:
            calendar = calendars.get(calendar_id, {})
            if calendar.get("errors") or not isinstance(calendar.get("busy"), list):
                raise ValueError(
                    f"Impossible de lire les disponibilités de l'agenda {calendar_id}"
                )
            for busy in calendar.get("busy", []):
                periods.collective.append(
                    (datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["end"]))
                )
    return periods


def update_manager_event(
    db: Session, settings: Settings, *, event_id: str, manager_email: str,
    title: str, description: str, start_at: datetime, end_at: datetime, attendees: list[str],
) -> str:
    credentials = _credentials(db, settings, manager_email, manager_events_scope(settings))
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    event = service.events().patch(
        calendarId=settings.google_target_calendar_id, eventId=event_id, sendUpdates="all",
        body={"summary": title, "description": description,
              "start": {"dateTime": start_at.isoformat()}, "end": {"dateTime": end_at.isoformat()},
              "attendees": [{"email": email} for email in dict.fromkeys(attendees) if email != manager_email],
              "guestsCanInviteOthers": False, "guestsCanModify": False},
    ).execute()
    return event["id"]


def delete_manager_event(db: Session, settings: Settings, *, event_id: str, manager_email: str) -> None:
    """Cancel only this session's event in the configured existing calendar."""
    calendar_id = settings.google_target_calendar_id.strip()
    if not calendar_id:
        raise ValueError("GOOGLE_TARGET_CALENDAR_ID est obligatoire")
    credentials = _credentials(db, settings, manager_email, manager_events_scope(settings))
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="all").execute()
    except HttpError as exc:
        # Already absent: allow a safe retry after a DB/network interruption.
        if exc.resp.status not in {404, 410}:
            raise


def create_manager_event(
    db: Session,
    settings: Settings,
    *,
    manager_email: str,
    title: str,
    description: str,
    start_at: datetime,
    end_at: datetime,
    attendees: list[str],
    request_key: str,
) -> str:
    credentials = _credentials(db, settings, manager_email, manager_events_scope(settings))
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    calendar_id = settings.google_target_calendar_id.strip()
    if not calendar_id:
        raise ValueError("GOOGLE_TARGET_CALENDAR_ID est obligatoire")
    event_id = hashlib.sha256(f"{settings.app_secret}:{request_key}".encode()).hexdigest()[:32]
    unique_attendees = list(
        dict.fromkeys(email.strip().lower() for email in attendees if email.strip().lower() != manager_email.lower())
    )
    try:
        event = service.events().insert(
            calendarId=calendar_id,
            sendUpdates="all",
            body={
                "id": event_id,
                "summary": title,
                "description": description,
                "start": {"dateTime": start_at.isoformat()},
                "end": {"dateTime": end_at.isoformat()},
                "attendees": [{"email": email} for email in unique_attendees],
                "guestsCanInviteOthers": False,
                "guestsCanModify": False,
            },
        ).execute()
    except HttpError as exc:
        if exc.resp.status != 409:
            raise
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    return event["id"]
