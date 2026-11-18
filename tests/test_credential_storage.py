"""API keys must be stored hashed, never in plaintext.

config advertises API_KEY_ENCRYPTION_KEY as a security setting while the
api_keys table held the raw key; it is now the HMAC key under which the
stored value is derived. Auth looks rows up by the hash of the presented
key, so the raw key exists only in the register response.

Self-contained: sets required env before importing the app, no conftest.
"""
import asyncio
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
from starlette.requests import Request

from middleware.auth import hash_api_key, verify_api_key
from routers.apps import RegisterAppRequest, register_app
from services.database import APIKey


def make_request(raw_key, path="/payments/checkout"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(b"x-api-key", raw_key.encode())],
        "client": ("127.0.0.1", 1234),
    }
    return Request(scope)


class RecordingDB:
    """Captures the filter value used to look the API key row up."""

    def __init__(self, key_row=None):
        self.key_row = key_row
        self.filter_values = []
        self.added = []
        self.committed = False

    def query(self, model):
        db = self

        class Q:
            def filter(self, *criteria):
                for c in criteria:
                    try:
                        db.filter_values.append(str(c.right.value))
                    except AttributeError:
                        pass
                return self

            def first(self):
                return db.key_row

        return Q()

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_hash_api_key_shape_and_determinism():
    raw = "pk_live_app_abcdefghijklmnopqrstuvwxyz012345"
    h1 = hash_api_key(raw)
    h2 = hash_api_key(raw)
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
    assert raw not in h1
    assert h1 != hash_api_key(raw + "x")


def test_register_app_stores_hash_not_raw_key():
    db = RecordingDB()
    req = RegisterAppRequest(app_name="myapp", app_display_name="My App")
    resp = asyncio.run(register_app(req, db))

    key_rows = [o for o in db.added if isinstance(o, APIKey)]
    assert len(key_rows) == 1
    stored = key_rows[0].api_key
    # The raw key is returned to the caller exactly once...
    assert resp.api_key.startswith("pk_live_myapp_")
    # ...but what lands in the row is the hash, never the raw value.
    assert stored != resp.api_key
    assert stored == hash_api_key(resp.api_key)


def test_verify_api_key_looks_up_by_hash_and_accepts_raw_key():
    raw = "pk_live_myapp_abcdefghijklmnopqrstuvwxyz012345"
    row = APIKey(
        app_name="myapp",
        api_key=hash_api_key(raw),
        api_key_prefix="pk_live_myapp",
        key_type="LIVE",
    )
    db = RecordingDB(key_row=row)
    app_name = asyncio.run(verify_api_key(make_request(raw), db))
    assert app_name == "myapp"
    assert db.committed
    # The lookup compares against the hash of the presented key, never the raw one.
    assert db.filter_values
    for value in db.filter_values:
        assert value == hash_api_key(raw)


def test_verify_api_key_rejects_unknown_key():
    db = RecordingDB(key_row=None)
    with pytest.raises(Exception) as exc:
        asyncio.run(verify_api_key(make_request("pk_live_myapp_wrong"), db))
    assert getattr(exc.value, "status_code", None) == 401
