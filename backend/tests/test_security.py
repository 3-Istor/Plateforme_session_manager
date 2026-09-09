import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.auth import current_user, manager_only
from backend.app.config import DEFAULT_TARGET_CALENDAR_ID, Settings


def test_demo_role_is_derived_from_server_configuration():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    manager = current_user(request=None, x_demo_user="lead@3istor.fr", db=None, settings=settings)
    member = current_user(request=None, x_demo_user="member@3istor.fr", db=None, settings=settings)
    assert manager.is_manager is True
    assert member.is_manager is False


def test_unknown_demo_user_is_rejected():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    with pytest.raises(HTTPException) as error:
        current_user(request=None, x_demo_user="intruder@example.com", db=None, settings=settings)
    assert error.value.status_code == 403


def test_manager_guard_cannot_be_bypassed_by_a_member():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    member = current_user(request=None, x_demo_user="member@3istor.fr", db=None, settings=settings)
    with pytest.raises(HTTPException) as error:
        manager_only(member)
    assert error.value.status_code == 403


def test_production_rejects_weak_or_insecure_configuration():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_secret="too-short",
            auth_mode="demo",
            frontend_url="http://example.com",
            google_redirect_uri="http://example.com/callback",
        )


def test_secure_production_configuration_uses_host_cookie():
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret="a" * 64,
        auth_mode="google",
        frontend_url="https://sessions.example.com",
        google_redirect_uri="https://sessions.example.com/api/google/calendar/callback",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="secret",
        google_target_calendar_id="team-calendar@group.calendar.google.com",
        manager_email="lead@example.com",
        team_members="lead@example.com,member@example.com",
    )
    assert settings.session_cookie_name == "__Host-3istor_session"


@pytest.mark.parametrize("target", [None, "", "   "])
def test_production_uses_existing_team_calendar_when_deployment_omits_target(monkeypatch, target):
    monkeypatch.delenv("GOOGLE_TARGET_CALENDAR_ID", raising=False)
    if target is not None:
        monkeypatch.setenv("GOOGLE_TARGET_CALENDAR_ID", target)
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret="a" * 64,
        auth_mode="google",
        frontend_url="https://sessions.example.com",
        allowed_hosts="sessions.example.com",
        google_redirect_uri="https://sessions.example.com/api/google/calendar/callback",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="test-secret",
        manager_email="lead@example.com",
        team_members="lead@example.com,member@example.com",
    )
    assert settings.google_target_calendar_id == DEFAULT_TARGET_CALENDAR_ID
    assert DEFAULT_TARGET_CALENDAR_ID in settings.availability_calendar_ids
