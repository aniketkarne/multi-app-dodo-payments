"""Outbound app callbacks must be verifiable against the exact bytes on the wire.

The signature used to be computed over `json.dumps(payload, sort_keys=True)`
while the request body went out through `httpx`'s `json=` serialization
(insertion order, different bytes). Any receiver that HMACs the raw request
body -- the standard practice in every webhook library, in every language --
rejected every callback. The signed string never crossed the wire.
"""
import asyncio
import hashlib
import hmac
import json
from typing import ClassVar

from services.database import AppRegistry
from services.webhook_service import WebhookService

SECRET = "test-webhook-signing-secret"

PAYLOAD = {
    "event": "payment.success",
    "app_name": "demo-app",
    "user_id": "user-1",
    "payment_id": "pay_123",
    "session_id": "cks_123",
    "product_id": "credits_50",
    "credits_granted": 50,
    "amount": 29.99,
    "currency": "USD",
    "timestamp": "2025-10-01T10:30:00Z",
}


class FakeResponse:
    status_code = 200
    text = ""


class FakeAsyncClient:
    captured: ClassVar[dict] = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.captured = {"url": url, **kwargs}
        return FakeResponse()


def _register_app(db):
    if db.query(AppRegistry).filter(AppRegistry.app_name == "demo-app").first():
        return
    db.add(AppRegistry(
        app_name="demo-app",
        app_display_name="Demo",
        app_secret_key="sk_test",
        callback_url="https://demo.example.com/hooks",
    ))
    db.commit()


def _send(db_session, monkeypatch):
    from services import webhook_service
    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", FakeAsyncClient)
    _register_app(db_session)
    ok = asyncio.run(WebhookService(db_session).send_app_callback("demo-app", dict(PAYLOAD)))
    assert ok is True
    return FakeAsyncClient.captured


def test_signature_matches_wire_bytes(db_session, monkeypatch):
    sent = _send(db_session, monkeypatch)

    # The request must carry the exact bytes that were signed.
    assert "content" in sent, "callback must send pre-serialized body bytes"
    body = sent["content"]
    assert isinstance(body, bytes)
    assert json.loads(body) == PAYLOAD

    signature = sent["headers"]["X-Webhook-Signature"]

    # Raw-body verification (what every non-Python webhook receiver does).
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, signature)


def test_readme_dict_verification_still_works(db_session, monkeypatch):
    # The README documents verification by re-serializing the parsed payload
    # with sort_keys=True. That must keep matching the signature too.
    sent = _send(db_session, monkeypatch)
    body = sent["content"]
    signature = sent["headers"]["X-Webhook-Signature"]

    canonical = json.dumps(json.loads(body), sort_keys=True).encode()
    assert canonical == body

    expected = hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, signature)


def test_callback_body_is_deterministic(db_session, monkeypatch):
    # Same payload twice -> same bytes -> same signature (receivers can cache
    # canonicalization; retries of a callback are verifiable).
    first = _send(db_session, monkeypatch)
    second = _send(db_session, monkeypatch)
    assert first["content"] == second["content"]
    assert first["headers"]["X-Webhook-Signature"] == second["headers"]["X-Webhook-Signature"]


def test_missing_callback_url_sends_nothing(db_session, monkeypatch):
    from services import webhook_service
    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.captured = {}
    db_session.add(AppRegistry(
        app_name="demo-app",
        app_display_name="Demo",
        app_secret_key="sk_test",
    ))
    db_session.commit()

    ok = asyncio.run(WebhookService(db_session).send_app_callback("demo-app", dict(PAYLOAD)))
    assert ok is False
    assert FakeAsyncClient.captured == {}
