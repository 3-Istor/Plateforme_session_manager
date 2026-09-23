import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import SecretStr

from backend.app import main
from backend.app.models import SessionRequest, RequestStatus
from backend.app.schemas import SessionCreate, DecisionIn, SessionOut
from backend.app.services import revisions, discord, user_calendar
from backend.tests.test_session_features import backend_context, request_payload, user, MEMBER, MANAGER, SECOND_MEMBER


def original(db, settings, approved=True):
    item = main.create_request(SessionCreate(**request_payload(12, 13)), BackgroundTasks(), db, user(MEMBER), settings)
    if approved:
        item.status = RequestStatus.approved
        item.calendar_event_id = "existing-event"
        db.commit()
    return item


def payload(start=12, end=13):
    return SessionCreate(**{**request_payload(start, end), "title": "Session modifiée"})


def test_member_proposal_leaves_original_unchanged_then_decline(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    old = revisions.snapshot(item)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    assert revisions.snapshot(item) == old
    assert change.modifies_request_id == item.id
    assert SessionOut.model_validate(change).previous_session["title"] == old["title"]
    main.decide_request(change.id, DecisionIn(status="declined"), db, user(MANAGER), settings)
    assert revisions.snapshot(item) == old
    assert change.status == RequestStatus.declined


def test_only_author_or_manager_can_modify_and_only_one_pending(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    with pytest.raises(HTTPException) as denied:
        main.propose_modification(item.id, payload(), db, user(SECOND_MEMBER), settings)
    assert denied.value.status_code == 403
    change = main.propose_modification(item.id, payload(), db, user(MANAGER), settings)
    assert change.requester_email == MANAGER
    with pytest.raises(HTTPException) as duplicate:
        main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    assert duplicate.value.status_code == 409
    assert change.id in [r.id for r in main.list_requests("mine", db, user(MEMBER))]


def test_accept_changes_original_without_duplicate_busy_periods(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    change = main.propose_modification(item.id, payload(14, 15), db, user(MEMBER), settings)
    main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert item.title == "Session modifiée"
    assert item.start_at == change.start_at
    assert item.calendar_event_id == "existing-event"
    busy = main.database_busy_periods(db, [MEMBER], change.start_at, change.end_at)
    assert len(busy.by_participant[MEMBER]) == 1
    with pytest.raises(HTTPException) as repeated:
        main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert repeated.value.status_code == 409


def test_stale_proposal_cannot_overwrite_later_change(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    item.agenda = "Une autre modification depuis la demande"
    db.commit()
    with pytest.raises(HTTPException) as stale:
        main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert stale.value.status_code == 409
    assert change.status == RequestStatus.pending


def test_pending_original_must_resolve_revision_first(backend_context):
    db, settings = backend_context
    item = original(db, settings, approved=False)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    with pytest.raises(HTTPException) as pending:
        main.decide_request(item.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert pending.value.status_code == 409
    main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert item.status == RequestStatus.approved


@pytest.mark.parametrize("failure", [False, True])
def test_google_approved_session_patches_existing_event(backend_context, monkeypatch, failure):
    db, settings = backend_context
    item = original(db, settings)
    old = revisions.snapshot(item)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    settings.auth_mode = "google"
    monkeypatch.setattr(main, "has_required_connection", lambda *args: True)
    calls = []
    def update(*args, **kwargs):
        calls.append(kwargs)
        if failure:
            raise RuntimeError("Google unavailable")
        return kwargs["event_id"]
    monkeypatch.setattr(revisions, "update_manager_event", update)
    monkeypatch.setattr(main, "create_manager_event", lambda *args, **kwargs: pytest.fail("Must not create duplicate event"))
    if failure:
        with pytest.raises(HTTPException) as error:
            main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
        assert error.value.status_code == 502
        assert revisions.snapshot(item) == old
        assert change.status == RequestStatus.pending
    else:
        main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
        assert item.title == "Session modifiée"
    assert calls[0]["event_id"] == "existing-event"


def test_conflicting_schedule_is_rechecked_before_approval(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    change = main.propose_modification(item.id, payload(14, 15), db, user(MEMBER), settings)
    main.create_request(SessionCreate(**request_payload(14, 15)), BackgroundTasks(), db, user(MEMBER), settings)
    with pytest.raises(HTTPException) as conflict:
        main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert conflict.value.status_code == 409
    assert item.start_at != change.start_at


def test_discord_revision_message_is_edited_after_approval(backend_context, monkeypatch):
    db, settings = backend_context
    settings.discord_webhook_url = SecretStr("https://discord.com/api/webhooks/123/token")
    item = original(db, settings)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    from backend.app.models import DiscordDelivery
    calls = []
    monkeypatch.setattr(discord, "discord_request", lambda url, method, body: calls.append((url, method, body)) or {"id": "456"})
    delivery = db.get(DiscordDelivery, change.id)
    discord.sync_delivery(db, delivery, settings)
    assert any("Modification" in f["value"] for f in calls[0][2]["embeds"][0]["fields"])
    main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    discord.sync_delivery(db, delivery, settings)
    assert calls[1][1] == "PATCH"
    assert calls[1][0].endswith("/messages/456")
    assert calls[1][2]["embeds"][0]["fields"][0]["value"] == "✅ Acceptée"


def test_calendar_patch_uses_same_target_id_and_notifies_attendees(monkeypatch):
    from backend.tests.test_user_calendar import settings
    calls = []
    class Service:
        def events(self): return self
        def patch(self, **kwargs): calls.append(kwargs); return self
        def execute(self): return {"id": "existing-event"}
    monkeypatch.setattr(user_calendar, "_credentials", lambda *args: object())
    monkeypatch.setattr(user_calendar, "build", lambda *args, **kwargs: Service())
    config = settings()
    data = payload()
    user_calendar.update_manager_event(None, config, event_id="existing-event", manager_email=MANAGER,
        title=data.title, description=data.agenda, start_at=data.start_at, end_at=data.end_at, attendees=[MEMBER])
    assert calls[0]["calendarId"] == config.google_target_calendar_id
    assert calls[0]["eventId"] == "existing-event"
    assert calls[0]["sendUpdates"] == "all"


def test_member_cannot_force_modification(backend_context):
    db, settings = backend_context
    item = original(db, settings)
    data = payload().model_copy(update={"force": True})
    with pytest.raises(HTTPException) as denied:
        main.propose_modification(item.id, data, db, user(MEMBER), settings)
    assert denied.value.status_code == 403


def test_forced_modification_requires_reconfirmation_for_new_conflicts(backend_context):
    from backend.tests.test_session_features import add_busy_request
    db, settings = backend_context
    item = original(db, settings)
    data = payload(14, 15).model_copy(update={"force": True})
    change = main.propose_modification(item.id, data, db, user(MANAGER), settings)
    add_busy_request(db, 14, 15, MEMBER)
    with pytest.raises(HTTPException) as changed:
        main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert changed.value.status_code == 409
    assert change.busy_participant_emails == [MEMBER]
    main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert item.is_forced
    assert item.busy_participant_emails == [MEMBER]


def test_added_participant_is_checked_even_at_same_time(backend_context):
    from backend.tests.test_session_features import add_busy_request
    db, settings = backend_context
    item = original(db, settings)
    add_busy_request(db, 12, 13, SECOND_MEMBER)
    data = payload().model_copy(update={"participant_emails": [MEMBER, SECOND_MEMBER]})
    with pytest.raises(HTTPException) as occupied:
        main.propose_modification(item.id, data, db, user(MEMBER), settings)
    assert occupied.value.status_code == 409


def test_pending_original_google_approval_creates_event_for_original_id(backend_context, monkeypatch):
    db, settings = backend_context
    item = original(db, settings, approved=False)
    change = main.propose_modification(item.id, payload(), db, user(MEMBER), settings)
    settings.auth_mode = "google"
    from backend.app.services.availability import BusyPeriods
    monkeypatch.setattr(main, "has_required_connection", lambda *args: True)
    monkeypatch.setattr(main, "google_busy_periods", lambda *args: BusyPeriods())
    calls = []
    monkeypatch.setattr(main, "create_manager_event", lambda *args, **kwargs: calls.append(kwargs) or "new-event")
    main.decide_request(change.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert item.calendar_event_id == "new-event"
    assert calls[0]["request_key"] == f"session-request-{item.id}"
