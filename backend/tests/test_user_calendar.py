from datetime import datetime, timezone
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.config import Settings
from backend.app.services import user_calendar
from backend.app.services.user_calendar import (
    EVENTS_SCOPE,
    FREEBUSY_SCOPE,
    SHARED_EVENTS_SCOPE,
    authorization_url,
    create_manager_event,
    create_oauth_state,
    decrypt_refresh_token,
    encrypt_refresh_token,
    freebusy_for_members,
    required_scopes,
    verify_target_calendar_write_access,
    verify_oauth_state,
)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_secret="local-test-secret",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="client-secret",
        manager_email="manager@gmail.com",
        team_members="manager@gmail.com,member@gmail.com",
        google_target_calendar_id="team-calendar@group.calendar.google.com",
    )


def test_member_receives_only_freebusy_calendar_scope():
    assert required_scopes(False) == [FREEBUSY_SCOPE]
    assert EVENTS_SCOPE not in required_scopes(False)


def test_only_manager_receives_event_creation_scope():
    assert set(required_scopes(True)) == {FREEBUSY_SCOPE, SHARED_EVENTS_SCOPE}
    assert EVENTS_SCOPE.endswith("calendar.app.created")
    assert SHARED_EVENTS_SCOPE in required_scopes(True)
    assert "https://www.googleapis.com/auth/calendar" not in required_scopes(True)


def test_existing_shared_calendar_uses_events_scope_for_manager_only():
    shared_settings = settings()
    shared_settings.google_target_calendar_id = "team@group.calendar.google.com"
    manager_query = parse_qs(
        urlparse(authorization_url(shared_settings, "manager@gmail.com", True)).query
    )
    member_query = parse_qs(
        urlparse(authorization_url(shared_settings, "member@gmail.com", False)).query
    )
    manager_scopes = set(manager_query["scope"][0].split())
    member_scopes = set(member_query["scope"][0].split())
    assert SHARED_EVENTS_SCOPE in manager_scopes
    assert EVENTS_SCOPE not in manager_scopes
    assert member_scopes.intersection({SHARED_EVENTS_SCOPE, EVENTS_SCOPE}) == set()


def test_refresh_token_is_encrypted_at_rest():
    encrypted = encrypt_refresh_token(settings(), "private-refresh-token")
    assert encrypted != "private-refresh-token"
    assert "private-refresh-token" not in encrypted
    assert decrypt_refresh_token(settings(), encrypted) == "private-refresh-token"


def test_oauth_state_is_signed_and_tamper_proof():
    state = create_oauth_state(settings(), "member@gmail.com", False)
    assert verify_oauth_state(settings(), state)["email"] == "member@gmail.com"
    with pytest.raises(ValueError):
        verify_oauth_state(settings(), state + "modified")


def test_member_authorization_url_never_requests_event_details():
    query = parse_qs(urlparse(authorization_url(settings(), "member@gmail.com", False)).query)
    scopes = set(query["scope"][0].split())
    assert FREEBUSY_SCOPE in scopes
    assert EVENTS_SCOPE not in scopes
    assert "https://www.googleapis.com/auth/calendar" not in scopes
    assert "https://www.googleapis.com/auth/userinfo.email" in scopes
    assert "include_granted_scopes" not in query


@pytest.mark.parametrize("manager", [False, True])
def test_pkce_proof_survives_new_flow_on_callback(monkeypatch, manager):
    from requests_oauthlib import OAuth2Session

    config = settings()
    email = "manager@gmail.com" if manager else "member@gmail.com"
    query = parse_qs(urlparse(authorization_url(config, email, manager)).query)
    state = query["state"][0]
    assert query["code_challenge_method"] == ["S256"]

    class TokenExchangeReached(Exception):
        pass

    def fetch_token(self, token_url, **kwargs):
        verifier = kwargs["code_verifier"]
        assert 43 <= len(verifier) <= 128
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert challenge == query["code_challenge"][0]
        assert verifier not in state
        assert verifier not in user_calendar._b64decode(state.split(".")[0]).decode()
        assert kwargs["code"] == "test-authorization-code"
        raise TokenExchangeReached

    monkeypatch.setattr(OAuth2Session, "fetch_token", fetch_token)
    # Fresh settings and Flow simulate a different request/process; no DB/network.
    with pytest.raises(TokenExchangeReached):
        user_calendar.exchange_code(None, settings(), code="test-authorization-code", state=state)


def test_pkce_flows_are_unique_even_at_same_time(monkeypatch):
    monkeypatch.setattr(user_calendar.time, "time", lambda: 1000)
    first = parse_qs(urlparse(authorization_url(settings(), "member@gmail.com", False)).query)
    second = parse_qs(urlparse(authorization_url(settings(), "member@gmail.com", False)).query)
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


@pytest.mark.parametrize("invalid", ["tampered", "expired"])
def test_invalid_state_rejected_before_token_exchange(monkeypatch, invalid):
    monkeypatch.setattr(user_calendar.time, "time", lambda: 1000)
    state = create_oauth_state(settings(), "member@gmail.com", False)
    if invalid == "tampered":
        state += "modified"
    else:
        monkeypatch.setattr(user_calendar.time, "time", lambda: 1000 + user_calendar.STATE_TTL_SECONDS + 1)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid state must not reach Google")

    monkeypatch.setattr(user_calendar.Flow, "from_client_config", forbidden)
    with pytest.raises(ValueError, match="État OAuth"):
        user_calendar.exchange_code(None, settings(), code="unused", state=state)


def test_collective_calendar_busy_periods_are_checked_once(monkeypatch):
    shared_settings = settings()
    shared_settings.google_availability_calendar_ids = (
        "deleguessigl@gmail.com, second@group.calendar.google.com,deleguessigl@gmail.com"
    )
    query_bodies = []
    credential_emails = []

    class FakeRequest:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class FakeService:
        def freebusy(self):
            return self

        def query(self, *, body):
            query_bodies.append(body)
            calendars = {
                item["id"]: {
                    "busy": [
                        {
                            "start": "2099-05-12T10:00:00+00:00",
                            "end": "2099-05-12T11:00:00+00:00",
                        }
                    ]
                }
                for item in body["items"]
            }
            return FakeRequest({"calendars": calendars})

    def fake_credentials(_db, _settings, email, _scope):
        credential_emails.append(email)
        return object()

    monkeypatch.setattr(user_calendar, "_credentials", fake_credentials)
    monkeypatch.setattr(user_calendar, "build", lambda *args, **kwargs: FakeService())

    periods = freebusy_for_members(
        None,
        shared_settings,
        ["manager@gmail.com", "member@gmail.com"],
        datetime(2099, 5, 12, tzinfo=timezone.utc),
        datetime(2099, 5, 13, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in query_bodies[0]["items"]] == ["primary"]
    assert [item["id"] for item in query_bodies[1]["items"]] == ["primary"]
    assert [item["id"] for item in query_bodies[2]["items"]] == [
        shared_settings.google_target_calendar_id,
        "deleguessigl@gmail.com",
        "second@group.calendar.google.com",
    ]
    assert credential_emails == [
        "manager@gmail.com",
        "member@gmail.com",
        "manager@gmail.com",
    ]
    assert len(periods) == 5
    assert len(periods.by_participant["manager@gmail.com"]) == 1
    assert len(periods.by_participant["member@gmail.com"]) == 1
    assert len(periods.collective) == 3


def test_event_is_inserted_into_the_exact_configured_calendar(monkeypatch):
    shared_settings = settings()
    shared_settings.google_target_calendar_id = "team-calendar@group.calendar.google.com"
    calls = []

    class FakeRequest:
        def execute(self):
            return {"id": "created-event"}

    class FakeEvents:
        def insert(self, **kwargs):
            calls.append(kwargs)
            return FakeRequest()

    class FakeService:
        def events(self):
            return FakeEvents()

    monkeypatch.setattr(user_calendar, "_credentials", lambda *args: object())
    monkeypatch.setattr(user_calendar, "build", lambda *args, **kwargs: FakeService())

    event_id = create_manager_event(
        None,
        shared_settings,
        manager_email="manager@gmail.com",
        title="Session test",
        description="Description",
        start_at=datetime(2099, 5, 12, 10, tzinfo=timezone.utc),
        end_at=datetime(2099, 5, 12, 11, tzinfo=timezone.utc),
        attendees=["manager@gmail.com", "member@gmail.com"],
        request_key="request-42",
    )

    assert event_id == "created-event"
    assert calls[0]["calendarId"] == "team-calendar@group.calendar.google.com"
    assert calls[0]["sendUpdates"] == "all"


def test_manager_connection_verifies_write_access_to_exact_calendar(monkeypatch):
    shared_settings = settings()
    calls = []

    class FakeRequest:
        def __init__(self, role):
            self.role = role

        def execute(self):
            return {"accessRole": self.role}

    class FakeEvents:
        def __init__(self, role):
            self.role = role

        def list(self, **kwargs):
            calls.append(kwargs)
            return FakeRequest(self.role)

    class FakeService:
        def __init__(self, role):
            self.role = role

        def events(self):
            return FakeEvents(self.role)

    monkeypatch.setattr(
        user_calendar,
        "build",
        lambda *args, **kwargs: FakeService("writer"),
    )
    verify_target_calendar_write_access(object(), shared_settings)
    assert calls == [
        {
            "calendarId": "team-calendar@group.calendar.google.com",
            "maxResults": 1,
            "fields": "accessRole",
        }
    ]

    monkeypatch.setattr(
        user_calendar,
        "build",
        lambda *args, **kwargs: FakeService("reader"),
    )
    with pytest.raises(ValueError, match="droit de modifier"):
        verify_target_calendar_write_access(object(), shared_settings)


def test_manager_connection_refuses_to_create_an_implicit_calendar():
    missing_target = settings()
    missing_target.google_target_calendar_id = ""
    with pytest.raises(ValueError, match="GOOGLE_TARGET_CALENDAR_ID"):
        authorization_url(missing_target, "manager@gmail.com", True)
