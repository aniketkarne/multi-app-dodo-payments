"""Admin authentication on /apps endpoints.

The /apps routes register apps (minting working API keys) and activate or
deactivate any app. They must require the X-Admin-Key header, and fail
closed when no ADMIN_API_KEY is configured.

Self-contained: sets required env before importing the app, no conftest.
"""
import os
import sys

os.environ.setdefault("DODO_API_KEY", "test-key")
os.environ.setdefault("DODO_BASE_URL", "https://example.invalid")
os.environ.setdefault("DODO_ENVIRONMENT", "test_mode")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
os.environ.setdefault("API_KEY_ENCRYPTION_KEY", "test-encryption-key-min-32-characters")
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "whsec_test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.config import get_settings
from routers.apps import router

ADMIN_KEY = "admin-secret-123"


def _set_admin_key(monkeypatch, value):
    monkeypatch.setenv("ADMIN_API_KEY", value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


def _client(db_stub=None):
    app = FastAPI()
    app.include_router(router)
    if db_stub is not None:
        from services.database import get_db

        def _override():
            yield db_stub

        app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _protected_requests(client, headers):
    return [
        client.post("/apps/register", json={"app_name": "a", "app_display_name": "A"}, headers=headers),
        client.get("/apps/a", headers=headers),
        client.put("/apps/a/deactivate", headers=headers),
        client.put("/apps/a/activate", headers=headers),
    ]


def test_all_apps_endpoints_reject_missing_admin_key(monkeypatch):
    _set_admin_key(monkeypatch, ADMIN_KEY)
    responses = _protected_requests(_client(), {})
    for r in responses:
        assert r.status_code == 401, r.text


def test_all_apps_endpoints_reject_wrong_admin_key(monkeypatch):
    _set_admin_key(monkeypatch, ADMIN_KEY)
    responses = _protected_requests(_client(), {"X-Admin-Key": "wrong"})
    for r in responses:
        assert r.status_code == 403, r.text


def test_all_apps_endpoints_fail_closed_when_unconfigured(monkeypatch):
    _set_admin_key(monkeypatch, "")
    # Even the correct-looking header is useless when nothing is configured.
    responses = _protected_requests(_client(), {"X-Admin-Key": ADMIN_KEY})
    for r in responses:
        assert r.status_code == 503, r.text
        assert "ADMIN_API_KEY" in r.json()["detail"]


def test_valid_admin_key_passes_auth(monkeypatch):
    _set_admin_key(monkeypatch, ADMIN_KEY)

    class StubDB:
        def query(self, model):
            class Q:
                def filter(self, *a, **k):
                    return self

                def first(self):
                    return None

            return Q()

        def close(self):
            pass

    client = _client(db_stub=StubDB())
    r = client.get("/apps/does-not-exist", headers={"X-Admin-Key": ADMIN_KEY})
    # Auth passed; the handler reached the DB and reported the missing app.
    assert r.status_code == 404
    assert r.json()["detail"] == "App not found"
