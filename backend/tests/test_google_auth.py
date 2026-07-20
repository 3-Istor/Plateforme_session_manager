from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.app import auth
from backend.app.config import Settings
from backend.app.database import Base
from backend.app.models import UserSession
from backend.app.schemas import User


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
    user = auth.authenticate_google_credential("valid-google-token", google_settings())
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
        auth.authenticate_google_credential("valid-google-token", google_settings())
    assert error.value.status_code == 403


def test_server_session_stores_only_a_hash_and_authenticates_cookie():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    settings = google_settings()
    with Session(engine) as db:
        token = auth.create_user_session(
            db,
            User(email="member@gmail.com", name="Membre Test", is_manager=False),
            settings,
        )
        stored = db.scalar(select(UserSession))
        assert stored is not None
        assert stored.token_hash != token
        assert token not in stored.token_hash

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/me",
                "headers": [(b"cookie", f"3istor_session={token}".encode())],
            }
        )
        user = auth.current_user(request=request, x_demo_user=None, db=db, settings=settings)
        assert user.email == "member@gmail.com"
        assert user.is_manager is False

        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        with pytest.raises(HTTPException) as error:
            auth.current_user(request=request, x_demo_user=None, db=db, settings=settings)
        assert error.value.status_code == 401
        assert db.get(UserSession, stored.token_hash) is None
