from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import current_user, display_name, manager_only
from .config import Settings, get_settings
from .database import Base, engine, get_db
from .models import CalendarConnection, Notification, Participant, RequestStatus, SessionRequest
from .schemas import (
    AvailabilityQuery,
    AuthorizationUrl,
    CalendarStatus,
    DecisionIn,
    Member,
    NotificationOut,
    SessionCreate,
    SessionOut,
    Slot,
    User,
)
from .services.availability import compute_slots
from .services.notifications import send_manager_email
from .services.user_calendar import (
    authorization_url,
    connected_emails,
    create_manager_event,
    exchange_code,
    freebusy_for_members,
    has_required_connection,
    verify_oauth_state,
)

Base.metadata.create_all(bind=engine)
settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(title="3istor Sessions API", version="1.0.0", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Demo-User"],
)

MEMBER_COLORS = ["#4f46e5", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#8b5cf6"]


def allowed_members(settings: Settings) -> list[str]:
    manager = str(settings.manager_email).lower()
    return list(dict.fromkeys([manager, *settings.member_emails]))


def validate_participants(emails: list[str], settings: Settings) -> list[str]:
    normalized = list(dict.fromkeys(str(email).lower() for email in emails))
    if not set(normalized).issubset(set(allowed_members(settings))):
        raise HTTPException(status_code=422, detail="Un ou plusieurs participants ne font pas partie de l'équipe")
    return normalized


def google_busy_periods(
    db: Session,
    settings: Settings,
    emails: list[str],
    start_at: datetime,
    end_at: datetime,
) -> list[tuple[datetime, datetime]]:
    if settings.auth_mode != "google":
        return []
    missing = [email for email in emails if not has_required_connection(db, settings, email, False)]
    if missing:
        raise HTTPException(
            status_code=409,
            detail="Agenda à connecter avant la planification : " + ", ".join(missing),
        )
    try:
        return freebusy_for_members(db, settings, emails, start_at, end_at)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Impossible de lire les disponibilités. Un membre doit peut-être reconnecter son agenda.",
        ) from exc


def database_busy_periods(
    db: Session, emails: list[str], start_at: datetime, end_at: datetime, exclude_id: int | None = None
) -> list[tuple[datetime, datetime]]:
    statement = (
        select(SessionRequest)
        .join(Participant)
        .where(
            Participant.email.in_(emails),
            SessionRequest.status.in_([RequestStatus.pending, RequestStatus.approved]),
            SessionRequest.start_at < end_at,
            SessionRequest.end_at > start_at,
        )
    )
    if exclude_id:
        statement = statement.where(SessionRequest.id != exclude_id)
    return [(item.start_at, item.end_at) for item in db.scalars(statement).unique().all()]


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def public_config(settings: Settings = Depends(get_settings)):
    return {
        "auth_mode": settings.auth_mode,
        "google_client_id": settings.google_client_id if settings.auth_mode == "google" else None,
        "calendar_connected": False,
        "working_hours": {"start": "08:00", "end": "21:00"},
    }


@app.get("/api/me", response_model=User)
def me(user: User = Depends(current_user)):
    return user


@app.get("/api/google/calendar/status", response_model=CalendarStatus)
def calendar_status(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return CalendarStatus(
        connected=has_required_connection(db, settings, str(user.email), False),
        can_create_events=has_required_connection(db, settings, str(user.email), True) if user.is_manager else False,
        connected_emails=[email for email in connected_emails(db) if email in allowed_members(settings)],
    )


@app.get("/api/google/calendar/connect", response_model=AuthorizationUrl)
def connect_calendar(
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    try:
        url = authorization_url(settings, str(user.email), user.is_manager)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AuthorizationUrl(authorization_url=url)


@app.get("/api/google/calendar/callback", include_in_schema=False)
def calendar_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    frontend = settings.frontend_url.rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{frontend}?{urlencode({'calendar_error': 'Autorisation Google annulée'})}")
    try:
        state_data = verify_oauth_state(settings, state)
        email = str(state_data["email"]).lower()
        if email not in allowed_members(settings):
            raise ValueError("Cette adresse ne fait pas partie de l'équipe")
        if bool(state_data["manager"]) != (email == str(settings.manager_email).lower()):
            raise ValueError("Le rôle de cette connexion a changé")
        exchange_code(db, settings, code=code, state=state)
    except Exception as exc:
        logger.exception("Google Calendar OAuth callback failed")
        message = (
            f"Connexion Calendar impossible : {str(exc)[:240]}"
            if settings.app_env != "production"
            else "Connexion Calendar impossible. Vérifiez la configuration Google."
        )
        return RedirectResponse(
            f"{frontend}?{urlencode({'calendar_error': message})}"
        )
    return RedirectResponse(f"{frontend}?calendar=connected")


@app.post("/api/google/calendar/disconnect", status_code=204)
def disconnect_calendar(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    connection = db.get(CalendarConnection, str(user.email))
    if connection:
        db.delete(connection)
        db.commit()


@app.get("/api/members", response_model=list[Member])
def members(_: User = Depends(current_user), settings: Settings = Depends(get_settings)):
    result = []
    for index, email in enumerate(allowed_members(settings)):
        name = display_name(email)
        initials = "".join(part[0] for part in name.split()[:2]).upper()
        result.append(Member(email=email, name=name, initials=initials, color=MEMBER_COLORS[index % len(MEMBER_COLORS)]))
    return result


@app.post("/api/availability", response_model=list[Slot])
def availability(
    query: AvailabilityQuery,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    emails = validate_participants([str(email) for email in query.participant_emails], settings)
    day_start = datetime.combine(query.day, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    busy = database_busy_periods(db, emails, day_start, day_end)
    busy.extend(google_busy_periods(db, settings, emails, day_start, day_end))
    return [
        Slot(start_at=start_at, end_at=end_at)
        for start_at, end_at in compute_slots(
            day=query.day,
            duration_minutes=query.duration_minutes,
            timezone_name=query.timezone,
            busy_periods=busy,
        )
    ]


@app.get("/api/requests", response_model=list[SessionOut])
def list_requests(
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = select(SessionRequest).order_by(SessionRequest.created_at.desc())
    if scope == "all":
        if not user.is_manager:
            raise HTTPException(status_code=403, detail="Vue réservée au manager")
    else:
        statement = statement.where(
            or_(
                SessionRequest.requester_email == str(user.email),
                SessionRequest.participants.any(Participant.email == str(user.email)),
            )
        )
    return db.scalars(statement).unique().all()


@app.post("/api/requests", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: SessionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    emails = validate_participants([str(email) for email in payload.participant_emails], settings)
    requester = str(user.email)
    if requester not in emails:
        emails.append(requester)
    if payload.start_at.tzinfo is None or payload.end_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Le fuseau horaire est obligatoire")
    if payload.start_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Le créneau doit être dans le futur")
    if payload.start_at.hour < 8 or payload.end_at.hour > 21 or (
        payload.end_at.hour == 21 and payload.end_at.minute > 0
    ):
        raise HTTPException(status_code=422, detail="Le créneau doit être compris entre 08:00 et 21:00")
    busy = database_busy_periods(db, emails, payload.start_at, payload.end_at)
    busy.extend(google_busy_periods(db, settings, emails, payload.start_at, payload.end_at))
    if any(payload.start_at < end_at and payload.end_at > start_at for start_at, end_at in busy):
        raise HTTPException(status_code=409, detail="Ce créneau vient d'être pris. Choisissez-en un autre.")

    item = SessionRequest(
        requester_email=requester,
        requester_name=user.name,
        title=payload.title.strip(),
        session_type=payload.session_type.strip(),
        agenda=payload.agenda.strip(),
        start_at=payload.start_at,
        end_at=payload.end_at,
        participants=[Participant(email=email) for email in emails],
    )
    db.add(item)
    db.flush()
    notification = Notification(
        recipient_email=str(settings.manager_email).lower(),
        title="Nouvelle demande de session",
        message=f"{user.name} demande « {item.title} ». Une validation est nécessaire.",
        request_id=item.id,
    )
    db.add(notification)
    db.commit()
    db.refresh(item)
    background_tasks.add_task(
        send_manager_email,
        settings,
        f"[3istor] Nouvelle demande — {item.title}",
        f"{user.name} a demandé une session du {item.start_at:%d/%m/%Y %H:%M} au {item.end_at:%H:%M}.\n\n{item.agenda}",
    )
    return item


@app.patch("/api/requests/{request_id}/decision", response_model=SessionOut)
def decide_request(
    request_id: int,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    _: User = Depends(manager_only),
    settings: Settings = Depends(get_settings),
):
    item = db.get(SessionRequest, request_id)
    if not item:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if item.status != RequestStatus.pending:
        raise HTTPException(status_code=409, detail="Cette demande a déjà été traitée")
    if payload.status == RequestStatus.approved:
        manager_email = str(settings.manager_email).lower()
        if settings.auth_mode == "google" and not has_required_connection(db, settings, manager_email, True):
            raise HTTPException(
                status_code=503,
                detail="Le manager doit connecter son Google Calendar avant de valider une session",
            )
        emails = [participant.email for participant in item.participants]
        busy = database_busy_periods(db, emails, item.start_at, item.end_at, exclude_id=item.id)
        busy.extend(google_busy_periods(db, settings, emails, item.start_at, item.end_at))
        if any(item.start_at < end_at and item.end_at > start_at for start_at, end_at in busy):
            raise HTTPException(status_code=409, detail="Un agenda est désormais occupé sur ce créneau")
        try:
            item.calendar_event_id = create_manager_event(
                db,
                settings,
                manager_email=manager_email,
                title=item.title,
                description=f"{item.session_type}\n\nOrdre du jour :\n{item.agenda}",
                start_at=item.start_at,
                end_at=item.end_at,
                attendees=emails,
                request_key=f"session-request-{item.id}",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="La création de l'événement Google a échoué") from exc
    item.status = payload.status
    item.manager_note = payload.manager_note.strip() if payload.manager_note else None
    notification_recipients = list(
        dict.fromkeys([item.requester_email, *(participant.email for participant in item.participants)])
    )
    for recipient in notification_recipients:
        calendar_message = (
            " et l'invitation Google Calendar a été envoyée"
            if item.calendar_event_id
            else " dans le planning de l'équipe"
        )
        db.add(
            Notification(
                recipient_email=recipient,
                title=(
                    "Session ajoutée au calendrier"
                    if item.status == RequestStatus.approved
                    else "Demande refusée"
                ),
                message=(
                    f"La session « {item.title} » a été validée{calendar_message}."
                    if item.status == RequestStatus.approved
                    else f"La demande « {item.title} » a été refusée."
                ),
                request_id=item.id,
            )
        )
    db.commit()
    db.refresh(item)
    return item


@app.get("/api/notifications", response_model=list[NotificationOut])
def notifications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    statement = (
        select(Notification)
        .where(Notification.recipient_email == str(user.email))
        .order_by(Notification.created_at.desc())
        .limit(30)
    )
    return db.scalars(statement).all()


@app.patch("/api/notifications/{notification_id}/read", status_code=204)
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    item = db.get(Notification, notification_id)
    if not item or item.recipient_email != str(user.email):
        raise HTTPException(status_code=404, detail="Notification introuvable")
    item.read_at = datetime.now(timezone.utc)
    db.commit()
