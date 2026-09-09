from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi import BackgroundTasks
from pydantic import SecretStr, ValidationError

from backend.app import main
from backend.app.config import Settings
from backend.app.models import DiscordDelivery
from backend.app.schemas import DecisionIn, SessionCreate
from backend.app.services import discord
from backend.tests.test_session_features import backend_context, request_payload, user, MANAGER, MEMBER


WEBHOOK = "https://discord.com/api/webhooks/123456/test_secret"


def create(db, settings):
    settings.discord_webhook_url = SecretStr(WEBHOOK)
    item = main.create_request(SessionCreate(**request_payload(12, 13)), BackgroundTasks(), db, user(MEMBER), settings)
    return item, db.get(DiscordDelivery, item.id)


@pytest.mark.parametrize("status,label", [("approved", "✅ Acceptée"), ("declined", "❌ Refusée")])
def test_send_then_edit_same_message_after_decision(backend_context, monkeypatch, status, label):
    db, settings = backend_context
    item, delivery = create(db, settings)
    calls = []
    def send(url, method, payload):
        calls.append((url, method, payload))
        return {"id": "987654"}
    monkeypatch.setattr(discord, "discord_request", send)
    discord.sync_delivery(db, delivery, settings)
    assert calls[0][0] == WEBHOOK + "?wait=true"
    assert calls[0][1] == "POST"
    fields = calls[0][2]["embeds"][0]["fields"]
    assert fields[0]["value"] == "⏳ En attente"
    assert "12:00–13:00" in fields[1]["value"]
    assert calls[0][2]["allowed_mentions"] == {"parse": []}
    discord.sync_delivery(db, delivery, settings)
    assert len(calls) == 1
    main.decide_request(item.id, DecisionIn(status=status, manager_note="Note @everyone"), db, user(MANAGER), settings)
    db.expire_all()  # Simulate reading the persisted ID in a later worker pass.
    discord.sync_delivery(db, db.get(DiscordDelivery, item.id), settings)
    assert calls[1][0] == WEBHOOK + "/messages/987654"
    assert calls[1][1] == "PATCH"
    assert calls[1][2]["embeds"][0]["fields"][0]["value"] == label
    assert calls[1][2]["embeds"][0]["fields"][-1]["value"] == "Note @everyone"


def test_failure_persists_retry_without_affecting_session_or_leaking_secret(backend_context, monkeypatch, caplog):
    db, settings = backend_context
    item, delivery = create(db, settings)
    def fail(*args):
        raise TimeoutError(WEBHOOK)
    monkeypatch.setattr(discord, "discord_request", fail)
    discord.sync_delivery(db, delivery, settings)
    db.expire_all()
    saved = db.get(DiscordDelivery, item.id)
    assert saved.failures == 1
    assert saved.retry_at > datetime.now(timezone.utc)
    assert "test_secret" not in caplog.text
    main.decide_request(item.id, DecisionIn(status="declined"), db, user(MANAGER), settings)
    sent = []
    monkeypatch.setattr(discord, "discord_request", lambda url, method, payload: sent.append(payload) or {"id": "123"})
    discord.sync_delivery(db, saved, settings)
    assert sent[0]["embeds"][0]["fields"][0]["value"] == "❌ Refusée"
    assert saved.failures == 0


def test_discord_rate_limit_respects_retry_after(backend_context, monkeypatch):
    db, settings = backend_context
    _, delivery = create(db, settings)
    def limited(*args):
        raise HTTPError(WEBHOOK, 429, "Rate limited", {}, BytesIO(b'{"retry_after": 600}'))
    monkeypatch.setattr(discord, "discord_request", limited)
    before = datetime.now(timezone.utc)
    discord.sync_delivery(db, delivery, settings)
    assert (delivery.retry_at - before).total_seconds() >= 600


def test_deleted_discord_message_is_recreated(backend_context, monkeypatch):
    db, settings = backend_context
    _, delivery = create(db, settings)
    delivery.message_id = "999"
    delivery.webhook_id = "123456"
    db.commit()
    def missing(*args):
        raise HTTPError(WEBHOOK, 404, "Missing", {}, BytesIO(b'{"code":10008}'))
    monkeypatch.setattr(discord, "discord_request", missing)
    discord.sync_delivery(db, delivery, settings)
    assert delivery.message_id is None
    calls = []
    monkeypatch.setattr(discord, "discord_request", lambda url, method, payload: calls.append(method) or {"id": "111"})
    discord.sync_delivery(db, delivery, settings)
    assert calls == ["POST"]


def test_disabled_integration_and_public_config(backend_context):
    db, settings = backend_context
    settings.discord_webhook_url = SecretStr("")
    item = main.create_request(SessionCreate(**request_payload(12, 13)), BackgroundTasks(), db, user(MEMBER), settings)
    assert db.get(DiscordDelivery, item.id) is None
    settings.discord_webhook_url = SecretStr(WEBHOOK)
    assert "discord" not in str(main.public_config(settings)).lower()
    assert "test_secret" not in repr(settings)


def test_worker_processes_persisted_deliveries(backend_context, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    db, settings = backend_context
    item, _ = create(db, settings)
    calls = []
    monkeypatch.setattr(discord, "discord_request", lambda url, method, payload: calls.append(method) or {"id": "444"})
    class OnePass:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, timeout):
            self.stopped = True
    discord.run_worker(OnePass(), sessionmaker(bind=db.get_bind()), settings)
    db.expire_all()
    assert db.get(DiscordDelivery, item.id).message_id == "444"
    discord.run_worker(OnePass(), sessionmaker(bind=db.get_bind()), settings)
    assert calls == ["POST"]


@pytest.mark.parametrize("url", ["http://discord.com/api/webhooks/123/token", "https://evil.example/api/webhooks/123/token", "https://discord.com.evil.example/api/webhooks/123/token"])
def test_webhook_only_accepts_discord_https(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, discord_webhook_url=url)
