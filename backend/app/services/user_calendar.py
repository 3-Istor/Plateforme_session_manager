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
from ..models import CalendarConnection, ManagedCalendar
from .availability import BusyPeriods

FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
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


def authorization_scopes(settings: Settings, is_manager: bool) -> list[str]:
    return [*IDENTITY_SCOPES, *required_scopes(is_manager, manager_events_scope(settings))]


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


def create_oauth_state(settings: Settings, email: str, is_manager: bool) -> str:
    payload = _b64encode(
        json.dumps(
            {"email": email.lower(), "manager": is_manager, "exp": int(time.time()) + STATE_TTL_SECONDS,
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


def authorization_url(settings: Settings, email: str, is_manager: bool) -> str:
    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET sont obligatoires")
    if is_manager and not settings.google_target_calendar_id.strip():
        raise ValueError("GOOGLE_TARGET_CALENDAR_ID est obligatoire pour le manager")
    state = create_oauth_state(settings, email, is_manager)
    flow = Flow.from_client_config(
        _client_config(settings), scopes=authorization_scopes(settings, is_manager), state=state,
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
    flow = Flow.from_client_config(
        _client_config(settings), scopes=authorization_scopes(settings, is_manager), state=state,
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
        credentials = _credentials(db, settings, email, FREEBUSY_SCOPE)
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        response = service.freebusy().query(
            body={
                "timeMin": start_at.isoformat(),
                "timeMax": end_at.isoformat(),
                "items": [{"id": "primary"}],
            }
        ).execute()
        calendars = response.get("calendars", {})
        primary = calendars.get("primary", {})
        if primary.get("errors") or not isinstance(primary.get("busy"), list):
            raise ValueError(f"Impossible de lire les disponibilités de l'agenda de {email}")
        for busy in primary.get("busy", []):
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
