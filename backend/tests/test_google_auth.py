import pytest
from fastapi import HTTPException

from backend.app import auth
from backend.app.config import Settings


def google_settings() -> Settings:
    return Settings(
        auth_mode="google",
        google_client_id="test-client.apps.googleusercontent.com",
        manager_email="manager@gmail.com",
        team_members="manager@gmail.com,member@gmail.com",
    )


def test_google_login_accepts_an_email_from_env(monkeypatch):
    monkeypatch.setattr(
        auth.id_token,
        "verify_oauth2_token",
        lambda *_args: {
            "email": "member@gmail.com",
            "email_verified": True,
            "name": "Membre Test",
        },
    )
    user = auth.current_user(
        authorization="Bearer valid-google-token",
        x_demo_user=None,
        settings=google_settings(),
    )
    assert user.email == "member@gmail.com"
    assert user.is_manager is False


def test_google_login_rejects_an_email_missing_from_env(monkeypatch):
    monkeypatch.setattr(
        auth.id_token,
        "verify_oauth2_token",
        lambda *_args: {
            "email": "unknown@gmail.com",
            "email_verified": True,
            "name": "Compte Inconnu",
        },
    )
    with pytest.raises(HTTPException) as error:
        auth.current_user(
            authorization="Bearer valid-google-token",
            x_demo_user=None,
            settings=google_settings(),
        )
    assert error.value.status_code == 403
