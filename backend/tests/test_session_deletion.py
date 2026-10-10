from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from pydantic import SecretStr
from sqlalchemy import select, text

from backend.app import main
from backend.app.auth import create_user_session
from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models import (DiscordDelivery, ForcedBusyParticipant, ForcedSession,
                                Notification, Participant, RequestStatus, SessionRequest, SessionRevision)
from backend.app.schemas import AvailabilityQuery, SessionCreate
from backend.app.services import user_calendar
from backend.app.services.availability import BusyPeriods
from backend.tests.test_session_features import (backend_context, request_payload, user,
                                                MEMBER, SECOND_MEMBER, MANAGER, add_busy_request)


def create(db, settings, requester=MEMBER, hour=12, force=False):
    return main.create_request(SessionCreate(**request_payload(hour, hour + 1, force=force)),
                               BackgroundTasks(), db, user(requester), settings)


def test_mine_only_includes_authored_requests_not_participation(backend_context):
    db, settings = backend_context
    own = create(db, settings)
    other = create(db, settings, SECOND_MEMBER, 14)
    assert MEMBER in [p.email for p in other.participants]
    assert [item.id for item in main.list_requests("mine", db, user(MEMBER))] == [own.id]
    assert [item.id for item in main.list_requests("mine", db, user(SECOND_MEMBER))] == [other.id]
    assert main.list_requests("mine", db, user(MANAGER)) == []
    assert {item.id for item in main.list_requests("all", db, user(MANAGER))} == {own.id, other.id}
    with pytest.raises(HTTPException) as error:
        main.list_requests("all", db, user(MEMBER))
    assert error.value.status_code == 403


@pytest.mark.parametrize("actor", [MEMBER, MANAGER])
@pytest.mark.parametrize("status", list(RequestStatus))
def test_owner_or_manager_can_delete_any_status(backend_context, actor, status):
    db, settings = backend_context
    item = create(db, settings)
    item.status = status
    db.commit()
    item_id = item.id
    assert main.delete_request(item_id, db, user(actor), settings).status_code == 204
    assert db.get(SessionRequest, item_id) is None
    assert list(db.scalars(select(Participant).where(Participant.request_id == item_id))) == []
    alerts = list(db.scalars(select(Notification)))
    assert alerts and all(alert.request_id is None for alert in alerts)
    assert {alert.recipient_email for alert in alerts} == {MEMBER, MANAGER}


def test_participant_cannot_delete_others_request(backend_context):
    db, settings = backend_context
    item = create(db, settings, SECOND_MEMBER)
    with pytest.raises(HTTPException) as error:
        main.delete_request(item.id, db, user(MEMBER), settings)
    assert error.value.status_code == 403
    assert db.get(SessionRequest, item.id) is not None
    with pytest.raises(HTTPException) as missing:
        main.delete_request(987654, db, user(MANAGER), settings)
    assert missing.value.status_code == 404


def test_delete_original_cleans_revisions_forced_rows_and_deliveries_with_foreign_keys(backend_context):
    db, settings = backend_context
    db.execute(text("PRAGMA foreign_keys=ON"))
    assert db.scalar(text("PRAGMA foreign_keys")) == 1
    settings.discord_webhook_url = SecretStr("https://discord.com/api/webhooks/123/token")
    add_busy_request(db, 12, 13, MEMBER)
    item = create(db, settings, force=True)
    data = SessionCreate(**{**request_payload(14, 15), "force": True})
    revision = main.propose_modification(item.id, data, db, user(MANAGER), settings)
    ids = [item.id, revision.id]
    assert db.get(DiscordDelivery, item.id)
    assert list(db.scalars(select(ForcedBusyParticipant).where(ForcedBusyParticipant.request_id == item.id)))
    main.delete_request(item.id, db, user(MEMBER), settings)
    for model in [SessionRequest, DiscordDelivery, ForcedSession]:
        assert all(db.get(model, key) is None for key in ids)
    assert db.get(SessionRevision, revision.id) is None
    assert not list(db.scalars(select(ForcedBusyParticipant).where(ForcedBusyParticipant.request_id.in_(ids))))
    assert not list(db.scalars(select(Notification).where(Notification.request_id.in_(ids))))
    assert not list(db.scalars(select(Participant).where(Participant.request_id.in_(ids))))


@pytest.mark.parametrize("approved", [False, True])
def test_delete_revision_never_cancels_original_google_event(backend_context, monkeypatch, approved):
    db, settings = backend_context
    item = create(db, settings)
    item.status = RequestStatus.approved
    item.calendar_event_id = "original-event"
    db.commit()
    revision = main.propose_modification(item.id, SessionCreate(**request_payload(12, 13)), db, user(MEMBER), settings)
    if approved:
        revision.status = RequestStatus.approved
        revision.calendar_event_id = "original-event"
        db.commit()
    settings.auth_mode = "google"
    monkeypatch.setattr(main, "delete_manager_event", lambda *a, **k: pytest.fail("Must not cancel original event"))
    main.delete_request(revision.id, db, user(MEMBER), settings)
    assert db.get(SessionRequest, item.id).calendar_event_id == "original-event"
    assert db.get(SessionRequest, revision.id) is None


@pytest.mark.parametrize("connected,failure", [(False, False), (True, True), (True, False)])
def test_google_delete_success_or_preserves_session_on_failure(backend_context, monkeypatch, connected, failure):
    db, settings = backend_context
    item = create(db, settings)
    item.status = RequestStatus.approved
    item.calendar_event_id = "original-event"
    db.commit()
    settings.auth_mode = "google"
    monkeypatch.setattr(main, "has_required_connection", lambda *args: connected)
    calls = []
    def remove(*args, **kwargs):
        calls.append(kwargs)
        if failure:
            raise RuntimeError("unavailable")
    monkeypatch.setattr(main, "delete_manager_event", remove)
    if connected and not failure:
        main.delete_request(item.id, db, user(MEMBER), settings)
        assert db.get(SessionRequest, item.id) is None
        assert calls == [{"event_id": "original-event", "manager_email": MANAGER}]
    else:
        with pytest.raises(HTTPException) as error:
            main.delete_request(item.id, db, user(MEMBER), settings)
        assert error.value.status_code == (502 if connected else 503)
        assert db.get(SessionRequest, item.id) is not None
        assert bool(calls) == connected


@pytest.mark.parametrize("status", [None, 404, 410, 403, 500])
def test_calendar_delete_exact_target_attendee_updates_and_idempotency(monkeypatch, status):
    from backend.tests.test_user_calendar import settings
    config = settings()
    calls = []
    class Service:
        def events(self): return self
        def delete(self, **kwargs): calls.append(kwargs); return self
        def execute(self):
            if status:
                raise HttpError(SimpleNamespace(status=status, reason="error"), b'{}')
    monkeypatch.setattr(user_calendar, "_credentials", lambda *args: object())
    monkeypatch.setattr(user_calendar, "build", lambda *args, **kwargs: Service())
    if status in {403, 500}:
        with pytest.raises(HttpError):
            user_calendar.delete_manager_event(None, config, event_id="one-event", manager_email=MANAGER)
    else:
        user_calendar.delete_manager_event(None, config, event_id="one-event", manager_email=MANAGER)
    assert calls == [{"calendarId": config.google_target_calendar_id, "eventId": "one-event", "sendUpdates": "all"}]


def test_manager_preview_excludes_self_but_checks_all_participants_and_collective_agendas(backend_context, monkeypatch):
    db, settings = backend_context
    item = create(db, settings)
    calls = []
    original = main.combined_busy_periods
    def busy(db, settings, emails, start, end, exclude_id=None):
        calls.append((emails, exclude_id, start, end))
        result = original(db, settings, emails, start, end, exclude_id)
        result.collective.append((item.start_at, item.end_at))
        return result
    monkeypatch.setattr(main, "combined_busy_periods", busy)
    result = main.request_calendar(item.id, None, db, user(MANAGER), settings)
    chosen = next(day for day in result.days if day.day == date(2099, 5, 12))
    assert calls[0][0] == [MANAGER, MEMBER]
    assert calls[0][1] == item.id
    assert all(block.busy_participant_emails == [] for block in chosen.busy)
    assert any(block.collective_calendar_busy for block in chosen.busy)
    assert "Nouvelle session" not in result.model_dump_json()


def test_modification_calendar_only_owner_or_manager_and_uses_proposed_participants(backend_context):
    db, settings = backend_context
    item = create(db, settings)
    add_busy_request(db, 14, 15, SECOND_MEMBER)
    query = AvailabilityQuery(day=date(2099, 5, 12), duration_minutes=15, participant_emails=[SECOND_MEMBER])
    for actor in [MEMBER, MANAGER]:
        result = main.modification_calendar(item.id, query, db, user(actor), settings)
        chosen = next(day for day in result.days if day.day == query.day)
        assert any(SECOND_MEMBER in block.busy_participant_emails for block in chosen.busy)
    with pytest.raises(HTTPException) as error:
        main.modification_calendar(item.id, query, db, user(SECOND_MEMBER), settings)
    assert error.value.status_code == 403


def test_reviewing_revision_excludes_original_and_proposal_reservations(backend_context):
    db, settings = backend_context
    item = create(db, settings)
    proposal = main.propose_modification(item.id, SessionCreate(**request_payload(14, 15)), db, user(MEMBER), settings)
    result = main.request_calendar(proposal.id, None, db, user(MANAGER), settings)
    assert all(not day.busy for day in result.days)


def test_discord_worker_ignores_delivery_deleted_between_poll_and_fetch(backend_context):
    from backend.app.services.discord import sync_delivery
    db, settings = backend_context
    sync_delivery(db, None, settings)


def test_http_routes_require_login_and_enforce_roles(backend_context, monkeypatch):
    db, settings = backend_context
    own = create(db, settings)
    other = create(db, settings, SECOND_MEMBER, 14)
    settings.auth_mode = "google"
    monkeypatch.setattr(main, "google_busy_periods", lambda *args: BusyPeriods())
    overrides = main.app.dependency_overrides.copy()
    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[get_db] = lambda: db
    query = {"day": "2099-05-12", "duration_minutes": 15, "participant_emails": [MEMBER]}
    try:
        with TestClient(main.app) as client:
            assert client.delete(f"/api/requests/{own.id}").status_code == 401
            assert client.get(f"/api/requests/{own.id}/calendar").status_code == 401
            assert client.post(f"/api/requests/{own.id}/availability", json=query).status_code == 401
            client.cookies.set(settings.session_cookie_name, create_user_session(db, user(MEMBER), settings))
            assert [row["id"] for row in client.get("/api/requests?scope=mine").json()] == [own.id]
            assert client.get("/api/requests?scope=all").status_code == 403
            assert client.delete(f"/api/requests/{other.id}").status_code == 403
            assert client.get(f"/api/requests/{own.id}/calendar").status_code == 403
            assert client.post(f"/api/requests/{other.id}/availability", json=query).status_code == 403
            assert client.post(f"/api/requests/{own.id}/availability", json=query).status_code == 200
            assert client.delete(f"/api/requests/{own.id}").status_code == 204
            client.cookies.clear()
            client.cookies.set(settings.session_cookie_name, create_user_session(db, user(MANAGER), settings))
            assert client.get(f"/api/requests/{other.id}/calendar").status_code == 200
            assert client.get(f"/api/requests/{other.id}/calendar?day=2099-05-19").status_code == 200
            assert client.delete(f"/api/requests/{other.id}").status_code == 204
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(overrides)
