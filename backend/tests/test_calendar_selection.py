from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import ValidationError
from sqlalchemy import text

from backend.app import main
from backend.app.auth import create_user_session
from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models import CalendarConnection, SelectedCalendar
from backend.app.schemas import AvailabilityQuery, CalendarSelectionIn, DecisionIn, SessionCreate
from backend.app.services import user_calendar as calendars
from backend.tests.test_session_features import backend_context, request_payload, user, MEMBER, SECOND_MEMBER, MANAGER

EXTRA = "interviews@group.calendar.google.com"
OTHER = "personal@group.calendar.google.com"
ALL_SCOPES = " ".join(sorted({calendars.FREEBUSY_SCOPE, *calendars.SELECTION_SCOPES}))


def connect(db, email, scopes=ALL_SCOPES):
    row = CalendarConnection(email=email, encrypted_refresh_token="test-encrypted-token", scopes=scopes)
    db.add(row); db.commit()
    return row


@pytest.fixture
def google(monkeypatch):
    state = {"calls": [], "failure": None}
    class Service:
        def __init__(self, email): self.email = email
        def calendarList(self): return self
        def freebusy(self): return self
        def list(self, **kwargs):
            state["calls"].append((self.email, "list", kwargs))
            if state["failure"] == "revoked": raise RefreshError("revoked")
            if state["failure"] == "rate_limit": raise HttpError(Response({"status": 429}), b"rate limit")
            if "pageToken" not in kwargs:
                self.result = {"items": [{"id": self.email, "summary": "Principal", "primary": True},
                    {"id": EXTRA, "summary": "Entretiens privés"}], "nextPageToken": "second-page"}
            else:
                self.result = {"items": [{"id": OTHER, "summary": "Personnel"},
                    {"id": "deleted", "summary": "Supprimé", "deleted": True},
                    {"id": "deleguessigl@gmail.com", "summary": "Délégués SIGL"}]}
            return self
        def query(self, *, body):
            ids = [item["id"] for item in body["items"]]
            state["calls"].append((self.email, "busy", ids))
            result = {key: {"busy": []} for key in ids}
            if EXTRA in ids:
                result[EXTRA] = {"busy": [{"start": "2099-05-12T10:00:00Z", "end": "2099-05-12T11:00:00Z"}]}
                if state["failure"] == "missing": del result[EXTRA]
                if state["failure"] == "denied": result[EXTRA] = {"errors": [{"reason": "forbidden"}]}
            self.result = {"calendars": result}
            return self
        def execute(self): return self.result

    def credentials(db, settings, email, scope):
        if scope not in calendars.connection_scopes(db.get(CalendarConnection, email)):
            raise ValueError("Permission manquante")
        return email
    monkeypatch.setattr(calendars, "_credentials", credentials)
    monkeypatch.setattr(calendars, "build", lambda *args, credentials, **kwargs: Service(credentials))
    return state


def test_old_connections_keep_primary_without_forced_reconnect(backend_context, google):
    db, settings = backend_context
    connect(db, MEMBER, calendars.FREEBUSY_SCOPE)
    connect(db, MANAGER, calendars.FREEBUSY_SCOPE)
    result = calendars.calendar_selection(db, settings, MEMBER)
    assert result["connected"] and result["authorization_required"]
    assert result["calendars"] == [{"id": "primary", "name": "Agenda principal", "primary": True, "selected": True, "available": True}]
    assert not google["calls"]
    calendars.freebusy_for_members(db, settings, [MEMBER], datetime(2099, 5, 12, tzinfo=timezone.utc), datetime(2099, 5, 13, tzinfo=timezone.utc))
    assert google["calls"][0] == (MEMBER, "busy", ["primary"])
    assert calendars.has_required_connection(db, settings, MEMBER, False)


def test_list_is_paginated_private_and_includes_hidden_calendars(backend_context, google):
    db, settings = backend_context
    connect(db, MEMBER); connect(db, SECOND_MEMBER)
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary", EXTRA])
    result = calendars.calendar_selection(db, settings, MEMBER)
    assert not result["authorization_required"]
    choices = {item["id"]: item for item in result["calendars"]}
    assert set(choices) == {"primary", EXTRA, OTHER}
    assert choices[EXTRA]["selected"] and choices[EXTRA]["name"] == "Entretiens privés"
    assert choices["primary"]["selected"] and choices["primary"]["primary"]
    assert not choices[OTHER]["selected"]
    assert all(not item["selected"] for item in calendars.calendar_selection(db, settings, SECOND_MEMBER)["calendars"] if not item["primary"])
    assert all(call[2]["showHidden"] and call[2]["fields"] == "nextPageToken,items(id,summary,primary,deleted)"
               for call in google["calls"] if call[1] == "list")
    assert "test-encrypted-token" not in str(result)


def test_add_remove_and_disconnect_preserve_other_members_and_google(backend_context, google):
    db, settings = backend_context
    db.execute(text("PRAGMA foreign_keys=ON"))
    connect(db, MEMBER); connect(db, SECOND_MEMBER)
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary", EXTRA, OTHER])
    calendars.save_calendar_selection(db, settings, SECOND_MEMBER, ["primary", OTHER])
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary", EXTRA])
    assert [row.calendar_id for row in calendars.selected_calendars(db, MEMBER)] == [EXTRA]
    main.disconnect_calendar(db, user(MEMBER))
    assert not calendars.selected_calendars(db, MEMBER)
    assert db.get(CalendarConnection, MEMBER) is None
    assert [row.calendar_id for row in calendars.selected_calendars(db, SECOND_MEMBER)] == [OTHER]
    assert all(call[1] == "list" for call in google["calls"])


def test_unavailable_selection_is_visible_and_removable_without_new_permissions(backend_context, google):
    db, settings = backend_context
    connect(db, MEMBER)
    db.add(SelectedCalendar(email=MEMBER, calendar_id="unavailable", name="Ancien agenda")); db.commit()
    result = calendars.calendar_selection(db, settings, MEMBER)
    missing = next(item for item in result["calendars"] if item["id"] == "unavailable")
    assert missing["selected"] and not missing["available"]
    db.get(CalendarConnection, MEMBER).scopes = calendars.FREEBUSY_SCOPE; db.commit()
    google["calls"].clear()
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary"])
    assert not calendars.selected_calendars(db, MEMBER)
    assert not google["calls"]


@pytest.mark.parametrize("calendar_id", ["guessed-private-calendar", "deleguessigl@gmail.com", "team-calendar@group.calendar.google.com"])
def test_cannot_add_unknown_or_mandatory_collective_calendars(backend_context, google, calendar_id):
    db, settings = backend_context
    connect(db, MEMBER)
    with pytest.raises(ValueError): calendars.save_calendar_selection(db, settings, MEMBER, [calendar_id])
    assert not calendars.selected_calendars(db, MEMBER)


@pytest.mark.parametrize("failure", [None, "missing", "denied"])
@pytest.mark.parametrize("view", ["new", "edit", "review"])
def test_secondary_calendar_blocks_all_views_and_booking_fail_closed(backend_context, google, failure, view):
    db, settings = backend_context
    original = main.create_request(SessionCreate(**request_payload(8, 9)), BackgroundTasks(), db, user(MEMBER), settings)
    connect(db, MEMBER); connect(db, MANAGER)
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary", EXTRA])
    settings.auth_mode = "google"; google["failure"] = failure
    query = AvailabilityQuery(day=date(2099, 5, 12), duration_minutes=60, participant_emails=[MEMBER, MANAGER])
    def read():
        if view == "edit": return main.modification_calendar(original.id, query, db, user(MEMBER), settings)
        if view == "review": return main.request_calendar(original.id, query.day, db, user(MANAGER), settings)
        return main.calendar_availability(query, db, user(MEMBER), settings)
    if failure:
        with pytest.raises(main.HTTPException) as error: read()
        assert error.value.status_code == 502
    else:
        result = read()
        chosen = next(day for day in result.days if day.day == query.day)
        slot = next(slot for slot in chosen.slots if slot.start_at.hour == 12 and slot.start_at.minute == 0)
        assert slot.busy_participant_emails == [MEMBER]
        assert any(MEMBER in block.busy_participant_emails for block in chosen.busy)
        assert "Entretiens privés" not in result.model_dump_json()
    with pytest.raises(main.HTTPException) as error:
        main.create_request(SessionCreate(**request_payload(12, 13)), BackgroundTasks(), db, user(MEMBER), settings)
    assert error.value.status_code == (502 if failure else 409)
    if not failure:
        assert (MANAGER, "busy", [settings.google_target_calendar_id, "deleguessigl@gmail.com"]) in google["calls"]


def test_secondary_calendar_rechecked_before_acceptance(backend_context, google, monkeypatch):
    db, settings = backend_context
    item = main.create_request(SessionCreate(**request_payload(12, 13)), BackgroundTasks(), db, user(MEMBER), settings)
    connect(db, MEMBER); connect(db, MANAGER)
    calendars.save_calendar_selection(db, settings, MEMBER, [EXTRA])
    settings.auth_mode = "google"
    monkeypatch.setattr(main, "has_required_connection", lambda *args: True)
    with pytest.raises(main.HTTPException) as error:
        main.decide_request(item.id, DecisionIn(status="approved"), db, user(MANAGER), settings)
    assert error.value.status_code == 409
    assert item.status.value == "pending"


@pytest.mark.parametrize("manage", [False, True])
def test_expanded_permissions_are_optional_signed_and_read_only(manage):
    settings = main.Settings(_env_file=None, google_client_id="test.apps.googleusercontent.com", google_client_secret="test-secret")
    query = parse_qs(urlparse(calendars.authorization_url(settings, MEMBER, False, manage)).query)
    scopes = set(query["scope"][0].split())
    assert calendars.SELECTION_SCOPES.issubset(scopes) == manage
    assert calendars.verify_oauth_state(settings, query["state"][0])["manage_calendars"] == manage
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert not scopes.intersection({calendars.SHARED_EVENTS_SCOPE, calendars.EVENTS_SCOPE, "https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/calendar.readonly"})


@pytest.mark.parametrize("missing", [None, calendars.CALENDAR_LIST_SCOPE, calendars.ACCESSIBLE_FREEBUSY_SCOPE])
def test_callback_validates_new_consent_preserves_selections_and_rejects_partial_grants(backend_context, monkeypatch, missing):
    db, settings = backend_context
    connection = connect(db, MEMBER, calendars.FREEBUSY_SCOPE)
    connection.encrypted_refresh_token = calendars.encrypt_refresh_token(settings, "original-test-token"); db.commit()
    db.add(SelectedCalendar(email=MEMBER, calendar_id=EXTRA, name="Entretiens privés")); db.commit()
    before = connection.encrypted_refresh_token
    scopes = {calendars.FREEBUSY_SCOPE, *calendars.SELECTION_SCOPES} - ({missing} if missing else set())
    credentials = SimpleNamespace(granted_scopes=scopes, scopes=scopes, id_token="test-id-token", refresh_token="new-test-token")
    flow = SimpleNamespace(credentials=credentials, fetch_token=lambda **kwargs: None)
    monkeypatch.setattr(calendars.Flow, "from_client_config", lambda *args, **kwargs: flow)
    monkeypatch.setattr(calendars.id_token, "verify_oauth2_token", lambda *args: {"email": MEMBER, "email_verified": True})
    state = calendars.create_oauth_state(settings, MEMBER, False, True)
    if missing:
        with pytest.raises(ValueError, match="Permissions Calendar manquantes"):
            calendars.exchange_code(db, settings, code="test-code", state=state)
        assert connection.encrypted_refresh_token == before
    else:
        response = main.calendar_callback("test-code", state, None, db, settings)
        assert "manage_calendars=1" in response.headers["location"]
        assert calendars.has_selection_permissions(db, MEMBER)
        assert calendars.decrypt_refresh_token(settings, connection.encrypted_refresh_token) == "new-test-token"
    assert [row.calendar_id for row in calendars.selected_calendars(db, MEMBER)] == [EXTRA]


def test_http_selection_is_owner_only_and_reconnect_retains_extra_permissions(backend_context, google):
    db, settings = backend_context
    connect(db, MEMBER); connect(db, SECOND_MEMBER)
    settings.auth_mode = "google"; settings.google_client_id = "test.apps.googleusercontent.com"; settings.google_client_secret = "test-secret"
    overrides = main.app.dependency_overrides.copy()
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/google/calendar/selection").status_code == 401
            assert client.patch("/api/google/calendar/selection", json={"calendar_ids": [EXTRA]}).status_code == 401
            client.cookies.set(settings.session_cookie_name, create_user_session(db, user(MEMBER), settings))
            assert client.patch("/api/google/calendar/selection", json={"calendar_ids": [EXTRA], "email": SECOND_MEMBER}).status_code == 422
            assert client.patch("/api/google/calendar/selection", json={"calendar_ids": ["primary", EXTRA]}).status_code == 204
            assert not calendars.selected_calendars(db, SECOND_MEMBER)
            assert client.get("/api/google/calendar/selection").json()["authorization_required"] is False
            google["failure"] = "revoked"
            assert client.get("/api/google/calendar/selection").status_code == 428
            google["failure"] = "rate_limit"
            assert client.get("/api/google/calendar/selection").status_code == 502
            google["failure"] = None
            query = parse_qs(urlparse(client.get("/api/google/calendar/connect").json()["authorization_url"]).query)
            assert calendars.SELECTION_SCOPES.issubset(query["scope"][0].split())
            assert client.post("/api/google/calendar/disconnect").status_code == 204
            assert not calendars.selected_calendars(db, MEMBER)
    finally:
        main.app.dependency_overrides.clear(); main.app.dependency_overrides.update(overrides)


@pytest.mark.parametrize("values", [[""], ["bad\ncalendar"], ["x" * 1025], [str(index) for index in range(51)]])
def test_invalid_calendar_selection_is_rejected(values):
    with pytest.raises(ValidationError): CalendarSelectionIn(calendar_ids=values)


def test_missing_permissions_block_addition_but_do_not_clear_previous_selection(backend_context, google):
    db, settings = backend_context
    connect(db, MEMBER, calendars.FREEBUSY_SCOPE)
    db.add(SelectedCalendar(email=MEMBER, calendar_id=OTHER, name="Personnel")); db.commit()
    with pytest.raises(calendars.CalendarAuthorizationRequired):
        calendars.save_calendar_selection(db, settings, MEMBER, [OTHER, EXTRA])
    assert [row.calendar_id for row in calendars.selected_calendars(db, MEMBER)] == [OTHER]
    assert not google["calls"]


def test_cancelled_google_consent_returns_to_selection_without_losing_data(backend_context):
    db, settings = backend_context
    connect(db, MEMBER)
    db.add(SelectedCalendar(email=MEMBER, calendar_id=EXTRA, name="Entretiens privés")); db.commit()
    state = calendars.create_oauth_state(settings, MEMBER, False, True)
    response = main.calendar_callback(None, state, "access_denied", db, settings)
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["manage_calendars"] == ["1"]
    assert query["calendar_error"] == ["Autorisation Google annulée"]
    assert [row.calendar_id for row in calendars.selected_calendars(db, MEMBER)] == [EXTRA]


def test_selection_limit_matches_google_freebusy_limit(backend_context, monkeypatch):
    db, settings = backend_context
    connect(db, MEMBER)
    choices = {f"calendar-{index}": {"id": f"calendar-{index}", "name": f"Agenda {index}", "primary": False} for index in range(50)}
    monkeypatch.setattr(calendars, "available_calendars", lambda *args: choices)
    selected = list(choices)[:49]
    calendars.save_calendar_selection(db, settings, MEMBER, ["primary", *selected])
    assert len(calendars.selected_calendars(db, MEMBER)) == 49
    with pytest.raises(ValueError, match="49"):
        calendars.save_calendar_selection(db, settings, MEMBER, list(choices))
    assert len(calendars.selected_calendars(db, MEMBER)) == 49


def test_new_table_can_be_added_without_changing_existing_connections():
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session
    from backend.app.database import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[table for name, table in Base.metadata.tables.items() if name != "selected_calendars"])
    with Session(engine) as db:
        connect(db, MEMBER, calendars.FREEBUSY_SCOPE)
        before = db.get(CalendarConnection, MEMBER).encrypted_refresh_token
        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)
        assert "selected_calendars" in inspect(engine).get_table_names()
        assert db.get(CalendarConnection, MEMBER).encrypted_refresh_token == before
        assert calendars.has_required_connection(db, None, MEMBER, False)
        assert not calendars.selected_calendars(db, MEMBER)
    engine.dispose()
