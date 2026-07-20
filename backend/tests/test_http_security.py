import asyncio

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.config import Settings
from backend.app.database import Base
from backend.app.schemas import GoogleCredentialIn, User


def make_request(method: str = "GET", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/test",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


async def empty_response(_request: Request) -> Response:
    return Response()


def test_api_responses_have_security_headers():
    response = asyncio.run(main.security_middleware(make_request(), empty_response))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_production_rejects_cross_site_mutation(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "frontend_url", "https://sessions.example.com")
    response = asyncio.run(
        main.security_middleware(
            make_request("POST", {"Origin": "https://attacker.example"}),
            empty_response,
        )
    )
    assert response.status_code == 403
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_large_request_is_rejected_before_parsing():
    response = asyncio.run(
        main.security_middleware(
            make_request("POST", {"Content-Length": "1000001"}),
            empty_response,
        )
    )
    assert response.status_code == 413


def test_production_login_cookie_is_secure_and_http_only(monkeypatch):
    settings = Settings(
        app_env="production",
        app_secret="a" * 64,
        auth_mode="google",
        frontend_url="https://sessions.example.com",
        allowed_hosts="sessions.example.com",
        google_redirect_uri="https://sessions.example.com/api/google/calendar/callback",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="secret",
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        main,
        "authenticate_google_credential",
        lambda *_args: User(email="member@gmail.com", name="Membre", is_manager=False),
    )
    request = type("RequestStub", (), {"client": type("Client", (), {"host": "127.0.0.1"})()})()
    response = Response()
    with Session(engine) as db:
        main.google_login(
            GoogleCredentialIn(credential="x" * 100),
            request,
            response,
            db,
            settings,
        )
    cookie = response.headers["set-cookie"]
    assert "__Host-3istor_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_google_login_is_rate_limited():
    main.login_attempts.clear()
    request = make_request("POST")
    for _ in range(main.LOGIN_RATE_LIMIT):
        main.enforce_login_rate_limit(request)
    with pytest.raises(HTTPException) as error:
        main.enforce_login_rate_limit(request)
    assert error.value.status_code == 429
    main.login_attempts.clear()
