from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import (
    authenticate_google_credential,
    create_user_session,
    current_user,
    delete_user_session,
    display_name,
    manager_only,
    profile_user,
)
from .config import Settings, get_settings
from .metrics import MetricsMiddleware
from .database import Base, engine, get_db, SessionLocal
from .models import (
    CalendarConnection,
    ForcedBusyParticipant,
    ForcedSession,
    LatenessRecord,
    Notification,
    Participant,
    RequestStatus,
    SessionRequest,
    UserProfile,
    DiscordDelivery,
)
from .schemas import (
    AvailabilityQuery,
    AuthorizationUrl,
    CalendarStatus,
    DecisionIn,
    ForcedSlot,
    GoogleCredentialIn,
    LatenessEntry,
    LatenessUpdate,
    Member,
    NotificationOut,
    SessionCreate,
    SessionOut,
    Slot,
    User,
    ProfileUpdate,
    RoleDecision,
)
from .services.availability import (
    BusyPeriods,
    compute_detailed_slots,
    compute_slots,
    periods_overlap,
    working_window,
)
from .services.notifications import send_manager_email
from .services.discord import run_worker
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

@asynccontextmanager
async def lifespan(app):
    stop = Event()
    worker = Thread(target=run_worker, args=(stop, SessionLocal, settings), daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=12)


app = FastAPI(
    lifespan=lifespan,
    title="3istor Sessions API",
    version="1.0.0",
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Demo-User"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[*settings.allowed_host_list, *(["testserver"] if settings.app_env != "production" else [])],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 1_000_000:
        response = JSONResponse(status_code=413, content={"detail": "Corps de requête trop volumineux"})
    elif (
        settings.app_env == "production"
        and request.url.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("origin", "").rstrip("/") != settings.frontend_url.rstrip("/")
    ):
        response = JSONResponse(status_code=403, content={"detail": "Origine de la requête refusée"})
    else:
        response = await call_next(request)

    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Last added is outermost: only public process metrics bypass host validation.
app.add_middleware(MetricsMiddleware)

MEMBER_COLORS = ["#4f46e5", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#8b5cf6"]
LOGIN_RATE_LIMIT = 20
LOGIN_RATE_WINDOW_SECONDS = 60
login_attempts: dict[str, deque[float]] = defaultdict(deque)
login_attempts_lock = Lock()
# This deployment runs one application instance. Serializing availability checks
# with writes closes the check/insert race within that instance.
scheduling_lock = Lock()
lateness_lock = Lock()


def enforce_login_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with login_attempts_lock:
        attempts = login_attempts[client]
        while attempts and attempts[0] <= now - LOGIN_RATE_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= LOGIN_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Trop de tentatives de connexion. Réessayez dans une minute.")
        attempts.append(now)


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
) -> BusyPeriods:
    if settings.auth_mode != "google":
        return BusyPeriods(by_participant={email: [] for email in emails})
    required_emails = list(emails)
    if settings.availability_calendar_ids:
        required_emails.append(str(settings.manager_email).lower())
    required_emails = list(dict.fromkeys(required_emails))
    missing = [
        email
        for email in required_emails
        if not has_required_connection(db, settings, email, False)
    ]
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
) -> BusyPeriods:
    statement = (
        select(Participant.email, SessionRequest.start_at, SessionRequest.end_at)
        .select_from(SessionRequest)
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
    result = BusyPeriods(by_participant={email: [] for email in emails})
    for email, busy_start, busy_end in db.execute(statement).all():
        result.by_participant.setdefault(email, []).append((busy_start, busy_end))
    return result


def combined_busy_periods(
    db: Session,
    settings: Settings,
    emails: list[str],
    start_at: datetime,
    end_at: datetime,
    exclude_id: int | None = None,
) -> BusyPeriods:
    return database_busy_periods(db, emails, start_at, end_at, exclude_id).merge(
        google_busy_periods(db, settings, emails, start_at, end_at)
    )


def has_conflict(busy: BusyPeriods, start_at: datetime, end_at: datetime) -> bool:
    return any(periods_overlap(start_at, end_at, period) for period in busy.all)


def busy_participant_emails(
    busy: BusyPeriods, start_at: datetime, end_at: datetime
) -> list[str]:
    return sorted(
        email
        for email, periods in busy.by_participant.items()
        if any(periods_overlap(start_at, end_at, period) for period in periods)
    )


def collective_calendar_has_conflict(
    busy: BusyPeriods, start_at: datetime, end_at: datetime
) -> bool:
    return any(
        periods_overlap(start_at, end_at, period) for period in busy.collective
    )


def refresh_forced_conflicts(
    item: SessionRequest,
    busy_emails: list[str],
    collective_busy: bool,
) -> bool:
    """Refresh a forced request snapshot and report whether it changed."""
    if not item.force_record:
        return False
    expected = set(busy_emails)
    current = {participant.email for participant in item.force_record.busy_participants}
    changed = (
        current != expected
        or item.force_record.collective_calendar_busy != collective_busy
    )
    if not changed:
        return False
    item.force_record.busy_participants[:] = [
        participant
        for participant in item.force_record.busy_participants
        if participant.email in expected
    ]
    retained = {
        participant.email for participant in item.force_record.busy_participants
    }
    item.force_record.busy_participants.extend(
        ForcedBusyParticipant(email=email) for email in sorted(expected - retained)
    )
    item.force_record.collective_calendar_busy = collective_busy
    return True


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


@app.post("/api/auth/google", response_model=User)
def google_login(
    payload: GoogleCredentialIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if settings.auth_mode != "google":
        raise HTTPException(status_code=404, detail="Connexion Google désactivée")
    enforce_login_rate_limit(request)
    user = authenticate_google_credential(payload.credential, settings)
    token = create_user_session(db, user, settings)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_minutes * 60,
        secure=settings.app_env == "production",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return user


@app.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    delete_user_session(db, request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.app_env == "production",
        httponly=True,
        samesite="lax",
    )


@app.get("/api/me", response_model=User)
def me(user: User = Depends(current_user)):
    return user


@app.patch("/api/profile", response_model=User)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db),
                   user: User = Depends(current_user), settings: Settings = Depends(get_settings)):
    profile = db.get(UserProfile, str(user.email))
    if profile is None:
        profile = UserProfile(email=str(user.email), manager_status="member")
        db.add(profile)
    profile.first_name = payload.first_name
    profile.last_name = payload.last_name
    if payload.request_manager and not user.is_manager and profile.manager_status != "pending":
        profile.manager_status = "pending"
        db.add(Notification(recipient_email=str(settings.manager_email).lower(),
                            title="Demande de rôle manager",
                            message=f"{payload.first_name} {payload.last_name} demande le rôle manager."))
    db.commit()
    return profile_user(db, str(user.email), user.name, settings)


@app.get("/api/profile/role-requests", response_model=list[User])
def role_requests(db: Session = Depends(get_db), _: User = Depends(manager_only),
                  settings: Settings = Depends(get_settings)):
    return [profile_user(db, p.email, display_name(p.email), settings)
            for p in db.scalars(select(UserProfile).where(
                UserProfile.manager_status == "pending",
                UserProfile.email.in_(allowed_members(settings)))).all()]


@app.patch("/api/profile/role-requests/{email}", response_model=User)
def decide_role(email: str, payload: RoleDecision, db: Session = Depends(get_db),
                manager: User = Depends(manager_only), settings: Settings = Depends(get_settings)):
    email = email.strip().lower()
    if email == str(manager.email):
        raise HTTPException(status_code=403, detail="Vous ne pouvez pas valider votre propre rôle")
    with scheduling_lock:
        profile = db.get(UserProfile, email)
        if profile is None or email not in allowed_members(settings):
            raise HTTPException(status_code=404, detail="Membre introuvable")
        if profile.manager_status != "pending":
            raise HTTPException(status_code=409, detail="Cette demande a déjà été traitée")
        profile.manager_status = "approved" if payload.approve else "declined"
        profile.reviewed_by = str(manager.email)
        db.add(Notification(recipient_email=email, title="Décision sur votre rôle",
                            message="Votre rôle manager a été validé." if payload.approve else "Votre demande de rôle manager a été refusée."))
        db.commit()
    return profile_user(db, email, display_name(email), settings)


@app.get("/api/google/calendar/status", response_model=CalendarStatus)
def calendar_status(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return CalendarStatus(
        connected=has_required_connection(db, settings, str(user.email), False),
        can_create_events=has_required_connection(db, settings, str(settings.manager_email), True) if user.is_manager else False,
        connected_emails=[email for email in connected_emails(db) if email in allowed_members(settings)],
    )


@app.get("/api/google/calendar/connect", response_model=AuthorizationUrl)
def connect_calendar(
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    try:
        url = authorization_url(settings, str(user.email), str(user.email) == str(settings.manager_email).lower())
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
def members(_: User = Depends(current_user), settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    result = []
    for index, email in enumerate(allowed_members(settings)):
        name = profile_user(db, email, display_name(email), settings).name
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
    day_start, day_end = working_window(query.day, query.timezone)
    busy = combined_busy_periods(db, settings, emails, day_start, day_end)
    return [
        Slot(start_at=start_at, end_at=end_at)
        for start_at, end_at in compute_slots(
            day=query.day,
            duration_minutes=query.duration_minutes,
            timezone_name=query.timezone,
            busy_periods=busy.all,
        )
    ]


@app.post("/api/availability/force", response_model=list[ForcedSlot])
def forced_availability(
    query: AvailabilityQuery,
    db: Session = Depends(get_db),
    _: User = Depends(manager_only),
    settings: Settings = Depends(get_settings),
):
    emails = validate_participants([str(email) for email in query.participant_emails], settings)
    day_start, day_end = working_window(query.day, query.timezone)
    busy = combined_busy_periods(db, settings, emails, day_start, day_end)
    return [
        ForcedSlot(
            start_at=slot.start_at,
            end_at=slot.end_at,
            busy_participant_emails=slot.busy_participant_emails,
            collective_calendar_busy=slot.collective_calendar_busy,
        )
        for slot in compute_detailed_slots(
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
    items = db.scalars(statement).unique().all()
    if user.is_manager:
        return items
    # The forced-slot endpoint and conflict identities are manager-only. Team
    # members can see that a request was forced without learning who was busy.
    return [
        SessionOut.model_validate(item).model_copy(
            update={
                "busy_participant_emails": [],
                "collective_calendar_busy": False,
            }
        )
        for item in items
    ]


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
    if payload.force and not user.is_manager:
        raise HTTPException(status_code=403, detail="Seul le manager peut forcer un créneau")
    if payload.start_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Le créneau doit être dans le futur")

    email_zone = ZoneInfo(payload.timezone)
    email_start = payload.start_at.astimezone(email_zone)
    email_end = payload.end_at.astimezone(email_zone)

    with scheduling_lock:
        busy = combined_busy_periods(db, settings, emails, payload.start_at, payload.end_at)
        if not payload.force and has_conflict(busy, payload.start_at, payload.end_at):
            raise HTTPException(status_code=409, detail="Ce créneau vient d'être pris. Choisissez-en un autre.")

        item = SessionRequest(
            requester_email=requester,
            requester_name=user.name,
            title=payload.formatted_title,
            session_type=payload.session_type,
            agenda=payload.agenda,
            start_at=payload.start_at,
            end_at=payload.end_at,
            participants=[Participant(email=email) for email in emails],
        )
        if payload.force:
            item.force_record = ForcedSession(
                collective_calendar_busy=collective_calendar_has_conflict(
                    busy, payload.start_at, payload.end_at
                ),
                busy_participants=[
                    ForcedBusyParticipant(email=email)
                    for email in busy_participant_emails(busy, payload.start_at, payload.end_at)
                ]
            )
        db.add(item)
        db.flush()
        if settings.discord_webhook_url.get_secret_value():
            db.add(DiscordDelivery(request_id=item.id))
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
        f"{user.name} a demandé une session du {email_start:%d/%m/%Y %H:%M} au {email_end:%H:%M}.\n\n{item.agenda}",
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
    with scheduling_lock:
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
            busy = combined_busy_periods(
                db,
                settings,
                emails,
                item.start_at,
                item.end_at,
                exclude_id=item.id,
            )
            if item.is_forced:
                latest_busy_emails = busy_participant_emails(
                    busy, item.start_at, item.end_at
                )
                latest_collective_busy = collective_calendar_has_conflict(
                    busy, item.start_at, item.end_at
                )
                if refresh_forced_conflicts(
                    item, latest_busy_emails, latest_collective_busy
                ):
                    db.commit()
                    db.refresh(item)
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Les indisponibilités de ce créneau forcé ont changé. "
                            "Relisez les conflits actualisés puis confirmez à nouveau."
                        ),
                    )
            else:
                if has_conflict(busy, item.start_at, item.end_at):
                    raise HTTPException(status_code=409, detail="Un agenda est désormais occupé sur ce créneau")
            if settings.auth_mode == "google":
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
        item.manager_note = payload.manager_note
        if settings.discord_webhook_url.get_secret_value() and db.get(DiscordDelivery, item.id) is None:
            db.add(DiscordDelivery(request_id=item.id))
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


def lateness_ranking(db: Session, settings: Settings) -> list[LatenessEntry]:
    records = {
        record.email: record
        for record in db.scalars(
            select(LatenessRecord).where(LatenessRecord.email.in_(allowed_members(settings)))
        ).all()
    }
    entries = [
        LatenessEntry(
            email=email,
            name=profile_user(db, email, display_name(email), settings).name,
            points=records[email].points if email in records else 0,
            updated_at=records[email].updated_at if email in records else None,
        )
        for email in allowed_members(settings)
    ]
    return sorted(entries, key=lambda entry: (-entry.points, entry.name.casefold(), str(entry.email)))


@app.get("/api/lateness", response_model=list[LatenessEntry])
def list_lateness(
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    return lateness_ranking(db, settings)


@app.patch("/api/lateness/{email}", response_model=LatenessEntry)
def update_lateness(
    email: str,
    payload: LatenessUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(manager_only),
    settings: Settings = Depends(get_settings),
):
    normalized = email.strip().lower()
    if normalized not in allowed_members(settings):
        raise HTTPException(status_code=404, detail="Membre introuvable")
    with lateness_lock:
        record = db.get(LatenessRecord, normalized)
        if record is None:
            record = LatenessRecord(email=normalized, points=payload.points)
            db.add(record)
        else:
            record.points = payload.points
        db.commit()
        db.refresh(record)
    return LatenessEntry(
        email=record.email,
        name=profile_user(db, record.email, display_name(record.email), settings).name,
        points=record.points,
        updated_at=record.updated_at,
    )


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


# Serve frontend static files in production or when built
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

dist_path = os.path.join(os.path.dirname(__file__), "../../frontend/dist")

if os.path.exists(dist_path):
    assets_path = os.path.join(dist_path, "assets")
    if os.path.exists(assets_path):
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    logo_path = os.path.join(dist_path, "3istor-logo.png")
    if os.path.exists(logo_path):
        @app.get("/3istor-logo.png", include_in_schema=False)
        def logo():
            return FileResponse(logo_path)

    @app.get("/{fallback_path:path}", include_in_schema=False)
    async def fallback(request: Request, fallback_path: str):
        if fallback_path == "api" or fallback_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        index_file = os.path.join(dist_path, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Not Found")
