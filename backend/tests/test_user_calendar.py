from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.config import Settings
from backend.app.services.user_calendar import (
    EVENTS_SCOPE,
    FREEBUSY_SCOPE,
    SHARED_EVENTS_SCOPE,
    authorization_url,
    create_oauth_state,
    decrypt_refresh_token,
    encrypt_refresh_token,
    required_scopes,
    verify_oauth_state,
)


def settings() -> Settings:
    return Settings(
        app_secret="local-test-secret",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="client-secret",
        manager_email="manager@gmail.com",
        team_members="manager@gmail.com,member@gmail.com",
    )


def test_member_receives_only_freebusy_calendar_scope():
    assert required_scopes(False) == [FREEBUSY_SCOPE]
    assert EVENTS_SCOPE not in required_scopes(False)


def test_only_manager_receives_event_creation_scope():
    assert set(required_scopes(True)) == {FREEBUSY_SCOPE, EVENTS_SCOPE}
    assert EVENTS_SCOPE.endswith("calendar.app.created")
    assert "calendar.events" not in required_scopes(True)
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
