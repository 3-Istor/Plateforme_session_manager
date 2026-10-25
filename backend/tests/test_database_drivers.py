"""Check production DB drivers too: SQLite alone cannot catch missing psycopg."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.mark.parametrize("url,driver,module", [
    ("postgresql://test:test@127.0.0.1:1/test", "psycopg", "psycopg"),
    ("postgresql+psycopg://test:test@127.0.0.1:1/test", "psycopg", "psycopg"),
    ("postgresql+psycopg2://test:test@127.0.0.1:1/test", "psycopg2", "psycopg2"),
    ("sqlite://", "pysqlite", "sqlite3.dbapi2"),
])
def test_database_driver_loads_without_connecting(url, driver, module):
    # SQLAlchemy imports the DBAPI at create_engine(), before opening a socket.
    # Port 1 is intentionally not a database: this test must never connect.
    engine = create_engine(url)
    try:
        assert engine.dialect.driver == driver
        assert engine.dialect.dbapi.__name__ == module
    finally:
        engine.dispose()


def test_psycopg_uses_self_contained_binary():
    # The slim production image does not install system libpq or build tools.
    import psycopg.pq
    assert psycopg.pq.__impl__ == "binary"


def test_postgresql_application_startup_and_booking():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to run the isolated PostgreSQL smoke test")
    parsed = make_url(url)
    # Never run schema creation against a production or user database by accident.
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "sessions_driver_test"
    env = {**os.environ, "APP_ENV": "test", "AUTH_MODE": "demo", "DATABASE_URL": url,
           "ALLOWED_HOSTS": "localhost,testserver", "DISCORD_WEBHOOK_URL": "", "SMTP_HOST": "",
           "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
           "MANAGER_EMAIL": "manager@example.com", "TEAM_MEMBERS": "manager@example.com,member@example.com"}
    # A subprocess bypasses conftest's deliberate SQLite-only environment and
    # imports exactly the same application module as the production entrypoint.
    script = '''
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import engine

assert engine.dialect.name == "postgresql"
assert engine.dialect.driver == "psycopg"
start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
headers = {"X-Demo-User": "manager@example.com"}
with TestClient(app) as client:
    assert client.get("/api/health").status_code == 200
    response = client.post("/api/requests", headers=headers, json={
        "title": "PostgreSQL startup test", "no_project": True, "session_type": "Travail",
        "agenda": "Validate PostgreSQL startup, schema creation, booking and approval.",
        "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(),
        "participant_emails": ["manager@example.com"], "timezone": "Europe/Paris",
    })
    assert response.status_code == 201, response.text
    item = response.json()
    response = client.patch(f"/api/requests/{item['id']}/decision", headers=headers, json={"status": "approved"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    rows = client.get("/api/requests?scope=all", headers=headers).json()
    assert any(row["id"] == item["id"] and row["status"] == "approved" for row in rows)
    response = client.delete(f"/api/requests/{item['id']}", headers=headers)
    assert response.status_code == 204, response.text
print("PostgreSQL application startup, schema, booking, approval and deletion passed")
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
