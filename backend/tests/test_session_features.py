from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.auth import manager_only
from backend.app.config import Settings
from backend.app.database import Base
from backend.app.models import (
    CalendarConnection,
    ManagedCalendar,
    Participant,
    RequestStatus,
    SessionRequest,
)
from backend.app.schemas import (
    AvailabilityQuery,
    DecisionIn,
    LatenessUpdate,
    SessionCreate,
    SessionOut,
    User,
    ProfileUpdate,
    RoleDecision,
)
from backend.app.services.availability import BusyPeriods
from backend.app.auth import profile_user
from backend.app.services.user_calendar import (
    FREEBUSY_SCOPE,
    SHARED_EVENTS_SCOPE,
    has_required_connection,
)


PARIS = ZoneInfo("Europe/Paris")
MANAGER = "manager@example.com"
MEMBER = "member@example.com"
SECOND_MEMBER = "second@example.com"


@pytest.fixture
def backend_context():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        auth_mode="demo",
        allowed_hosts="localhost,testserver",
        manager_email=MANAGER,
        team_members=f"{MANAGER},{MEMBER},{SECOND_MEMBER}",
        google_target_calendar_id="team-calendar@group.calendar.google.com",
    )
    with Session(test_engine, expire_on_commit=False) as db:
        yield db, settings
    Base.metadata.drop_all(test_engine)


def user(email: str) -> User:
    return User(
        email=email,
        name=email.split("@", 1)[0].title(),
        is_manager=email == MANAGER,
    )


def add_busy_request(
    db: Session,
    start_hour: int,
    end_hour: int,
    participant_email: str = MEMBER,
) -> int:
    item = SessionRequest(
        requester_email=participant_email,
        requester_name=participant_email.split("@", 1)[0].title(),
        title="Session existante",
        session_type="Travail",
        agenda="Ordre du jour existant",
        start_at=datetime(2099, 5, 12, start_hour, tzinfo=PARIS),
        end_at=datetime(2099, 5, 12, end_hour, tzinfo=PARIS),
        status=RequestStatus.pending,
        participants=[Participant(email=participant_email)],
    )
    db.add(item)
    db.commit()
    return item.id


def request_payload(start_hour: int, end_hour: int, *, force: bool = False) -> dict:
    return {
        "title": "  Nouvelle session  ",
        "session_type": "  Travail  ",
        "agenda": "  Un ordre du jour suffisamment précis.  ",
        "start_at": datetime(2099, 5, 12, start_hour, tzinfo=PARIS),
        "end_at": datetime(2099, 5, 12, end_hour, tzinfo=PARIS),
        "participant_emails": [MANAGER, MEMBER],
        "force": force,
        "timezone": "Europe/Paris",
    }


def test_new_api_routes_are_registered():
    paths = main.app.openapi()["paths"]
    assert "post" in paths["/api/availability/force"]
    assert "get" in paths["/api/lateness"]
    assert "patch" in paths["/api/lateness/{email}"]


def test_profile_role_requires_manager_approval(backend_context):
    db, settings = backend_context
    saved = main.update_profile(ProfileUpdate(first_name=" Alice ", last_name=" Martin ", request_manager=True), db, user(MEMBER), settings)
    assert saved.name == "Alice Martin"
    assert saved.first_name == "Alice"
    assert saved.manager_status == "pending"
    assert saved.is_manager is False
    with pytest.raises(HTTPException):
        manager_only(saved)
    with pytest.raises(ValidationError):
        ProfileUpdate(first_name="Alice", last_name="Martin", is_manager=True)
    pending = main.role_requests(db, manager_only(user(MANAGER)), settings)
    assert [str(p.email) for p in pending] == [MEMBER]
    approved = main.decide_role(MEMBER, RoleDecision(approve=True), db, manager_only(user(MANAGER)), settings)
    assert approved.is_manager is True
    db.expire_all()
    assert profile_user(db, MEMBER, "Old name", settings).name == "Alice Martin"
    assert manager_only(profile_user(db, MEMBER, "Old name", settings)).is_manager
    assert next(p for p in main.lateness_ranking(db, settings) if str(p.email) == MEMBER).name == "Alice Martin"


def test_role_refusal_and_self_approval(backend_context):
    db, settings = backend_context
    pending = main.update_profile(ProfileUpdate(first_name="Bob", last_name="Martin", request_manager=True), db, user(MEMBER), settings)
    with pytest.raises(HTTPException) as error:
        main.decide_role(MEMBER, RoleDecision(approve=True), db, pending, settings)
    assert error.value.status_code == 403
    rejected = main.decide_role(MEMBER, RoleDecision(approve=False), db, manager_only(user(MANAGER)), settings)
    assert rejected.is_manager is False
    assert rejected.manager_status == "declined"
    with pytest.raises(ValidationError):
        ProfileUpdate(first_name="  ", last_name="Martin")


def test_manager_connection_is_ready_only_for_verified_exact_target(
    backend_context,
):
    db, settings = backend_context
    db.add(
        CalendarConnection(
            email=MANAGER,
            encrypted_refresh_token="encrypted",
            scopes=f"{FREEBUSY_SCOPE} {SHARED_EVENTS_SCOPE}",
        )
    )
    db.commit()
    assert has_required_connection(db, settings, MANAGER, False) is True
    assert has_required_connection(db, settings, MANAGER, True) is False

    db.add(
        ManagedCalendar(
            email=MANAGER,
            calendar_id=settings.google_target_calendar_id,
        )
    )
    db.commit()
    assert has_required_connection(db, settings, MANAGER, True) is True

    settings.google_target_calendar_id = "another-calendar@group.calendar.google.com"
    assert has_required_connection(db, settings, MANAGER, True) is False


def test_forced_availability_is_manager_only_and_identifies_busy_members(backend_context):
    db, settings = backend_context
    add_busy_request(db, 10, 11)
    query = AvailabilityQuery(
        day="2099-05-12",
        duration_minutes=60,
        participant_emails=[MANAGER, MEMBER],
        timezone="Europe/Paris",
    )

    with pytest.raises(HTTPException) as forbidden:
        main.forced_availability(query, db, manager_only(user(MEMBER)), settings)
    assert forbidden.value.status_code == 403

    regular = main.availability(query, db, user(MEMBER), settings)
    forced = main.forced_availability(query, db, manager_only(user(MANAGER)), settings)
    regular_starts = {slot.start_at.astimezone(PARIS).strftime("%H:%M") for slot in regular}
    forced_by_start = {
        slot.start_at.astimezone(PARIS).strftime("%H:%M"): slot for slot in forced
    }
    assert "09:30" not in regular_starts
    assert "10:00" not in regular_starts
    assert "10:30" not in regular_starts
    assert forced_by_start["10:00"].busy_participant_emails == [MEMBER]
    assert forced_by_start["10:00"].collective_calendar_busy is False
    assert forced_by_start["08:00"].busy_participant_emails == []
    assert len(forced) == 25


def test_forced_request_is_persisted_and_demo_approval_never_calls_google(
    backend_context, monkeypatch
):
    db, settings = backend_context
    add_busy_request(db, 10, 11)

    with pytest.raises(HTTPException) as forbidden:
        main.create_request(
            SessionCreate(**request_payload(10, 11, force=True)),
            BackgroundTasks(),
            db,
            user(MEMBER),
            settings,
        )
    assert forbidden.value.status_code == 403

    created = main.create_request(
        SessionCreate(**request_payload(10, 11, force=True)),
        BackgroundTasks(),
        db,
        user(MANAGER),
        settings,
    )
    body = SessionOut.model_validate(created)
    assert body.title == "Nouvelle session"
    assert body.session_type == "Travail"
    assert body.agenda == "Un ordre du jour suffisamment précis."
    assert body.is_forced is True
    assert body.busy_participant_emails == [MEMBER]

    member_view = main.list_requests("mine", db, user(MEMBER))
    manager_view = main.list_requests("mine", db, user(MANAGER))
    assert member_view[0].is_forced is True
    assert member_view[0].busy_participant_emails == []
    assert manager_view[0].busy_participant_emails == [MEMBER]

    def unexpected_google_call(*args, **kwargs):
        raise AssertionError("Google Calendar must not be called in demo mode")

    monkeypatch.setattr(main, "create_manager_event", unexpected_google_call)
    approved = main.decide_request(
        created.id,
        DecisionIn(status="approved", manager_note="  Forcé volontairement  "),
        db,
        manager_only(user(MANAGER)),
        settings,
    )
    response = SessionOut.model_validate(approved)
    assert response.status == RequestStatus.approved
    assert response.manager_note == "Forcé volontairement"
    assert response.is_forced is True
    assert response.busy_participant_emails == [MEMBER]

    db.expire_all()
    persisted = db.get(SessionRequest, created.id)
    persisted_response = SessionOut.model_validate(persisted)
    assert persisted_response.is_forced is True
    assert persisted_response.busy_participant_emails == [MEMBER]


def test_forced_approval_requires_reconfirmation_when_conflicts_change(
    backend_context,
):
    db, settings = backend_context
    add_busy_request(db, 10, 11, MEMBER)
    created = main.create_request(
        SessionCreate(**request_payload(10, 11, force=True)),
        BackgroundTasks(),
        db,
        user(MANAGER),
        settings,
    )
    add_busy_request(db, 10, 11, MANAGER)

    with pytest.raises(HTTPException) as changed:
        main.decide_request(
            created.id,
            DecisionIn(status="approved"),
            db,
            manager_only(user(MANAGER)),
            settings,
        )
    assert changed.value.status_code == 409

    db.expire_all()
    refreshed = db.get(SessionRequest, created.id)
    assert refreshed.status == RequestStatus.pending
    assert refreshed.busy_participant_emails == [MANAGER, MEMBER]

    approved = main.decide_request(
        created.id,
        DecisionIn(status="approved"),
        db,
        manager_only(user(MANAGER)),
        settings,
    )
    assert approved.status == RequestStatus.approved


def test_collective_conflict_is_persisted_for_forced_request(
    backend_context, monkeypatch
):
    db, settings = backend_context

    def collective_busy(_db, _settings, emails, start_at, end_at, exclude_id=None):
        return BusyPeriods(
            by_participant={email: [] for email in emails},
            collective=[(start_at, end_at)],
        )

    monkeypatch.setattr(main, "combined_busy_periods", collective_busy)
    created = main.create_request(
        SessionCreate(**request_payload(14, 15, force=True)),
        BackgroundTasks(),
        db,
        user(MANAGER),
        settings,
    )
    response = SessionOut.model_validate(created)
    assert response.is_forced is True
    assert response.busy_participant_emails == []
    assert response.collective_calendar_busy is True


def test_normal_request_is_checked_at_creation_and_again_at_approval(backend_context):
    db, settings = backend_context
    add_busy_request(db, 10, 11)

    with pytest.raises(HTTPException) as conflict:
        main.create_request(
            SessionCreate(**request_payload(10, 11)),
            BackgroundTasks(),
            db,
            user(MEMBER),
            settings,
        )
    assert conflict.value.status_code == 409

    created = main.create_request(
        SessionCreate(**request_payload(12, 13)),
        BackgroundTasks(),
        db,
        user(MEMBER),
        settings,
    )
    assert created.is_forced is False
    assert created.busy_participant_emails == []

    add_busy_request(db, 12, 13)
    with pytest.raises(HTTPException) as approval:
        main.decide_request(
            created.id,
            DecisionIn(status="approved"),
            db,
            manager_only(user(MANAGER)),
            settings,
        )
    assert approval.value.status_code == 409


def test_normal_google_request_rechecks_the_exact_interval_on_approval(
    backend_context, monkeypatch
):
    db, settings = backend_context
    settings.auth_mode = "google"
    calls = []

    def empty_google_busy(_db, _settings, emails, start_at, end_at):
        calls.append((start_at, end_at))
        return BusyPeriods(by_participant={email: [] for email in emails})

    monkeypatch.setattr(main, "google_busy_periods", empty_google_busy)
    monkeypatch.setattr(main, "has_required_connection", lambda *args: True)
    monkeypatch.setattr(main, "create_manager_event", lambda *args, **kwargs: "event-id")

    payload = SessionCreate(**request_payload(15, 16))
    created = main.create_request(
        payload,
        BackgroundTasks(),
        db,
        user(MEMBER),
        settings,
    )
    approved = main.decide_request(
        created.id,
        DecisionIn(status="approved"),
        db,
        manager_only(user(MANAGER)),
        settings,
    )

    assert approved.calendar_event_id == "event-id"
    assert calls == [
        (payload.start_at, payload.end_at),
        (payload.start_at, payload.end_at),
    ]


def test_session_schema_trims_text_and_enforces_slot_invariants():
    valid = SessionCreate(**request_payload(8, 9))
    assert valid.title == "Nouvelle session"
    assert valid.session_type == "Travail"
    assert valid.agenda == "Un ordre du jour suffisamment précis."

    invalid_payloads = [
        {**request_payload(8, 9), "title": "   "},
        {
            **request_payload(8, 9),
            "end_at": datetime(2099, 5, 12, 8, 45, tzinfo=PARIS),
        },
        {
            **request_payload(8, 9),
            "end_at": datetime(2099, 5, 13, 8, tzinfo=PARIS),
        },
        {
            **request_payload(8, 9),
            "start_at": datetime(2099, 5, 12, 7, 30, tzinfo=PARIS),
        },
        {**request_payload(8, 9), "timezone": "Invalid/Timezone"},
        {**request_payload(8, 9), "timezone": "UTC"},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            SessionCreate(**payload)


def test_lateness_is_visible_to_team_but_only_manager_can_update(backend_context):
    db, settings = backend_context
    initial = main.list_lateness(db, user(MEMBER), settings)
    assert {str(entry.email) for entry in initial} == {MANAGER, MEMBER, SECOND_MEMBER}
    assert all(entry.points == 0 for entry in initial)

    with pytest.raises(HTTPException) as forbidden:
        main.update_lateness(
            MEMBER,
            LatenessUpdate(points=4),
            db,
            manager_only(user(MEMBER)),
            settings,
        )
    assert forbidden.value.status_code == 403

    updated = main.update_lateness(
        MEMBER,
        LatenessUpdate(points=4),
        db,
        manager_only(user(MANAGER)),
        settings,
    )
    assert updated.points == 4
    assert updated.updated_at is not None

    ranking = main.list_lateness(db, user(SECOND_MEMBER), settings)
    assert str(ranking[0].email) == MEMBER
    assert ranking[0].points == 4

    with pytest.raises(ValidationError):
        LatenessUpdate(points=-1)
    with pytest.raises(ValidationError):
        LatenessUpdate(points=1_000_001)
    with pytest.raises(HTTPException) as unknown:
        main.update_lateness(
            "unknown@example.com",
            LatenessUpdate(points=1),
            db,
            manager_only(user(MANAGER)),
            settings,
        )
    assert unknown.value.status_code == 404
