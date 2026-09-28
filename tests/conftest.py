"""
Pytest configuration for the multi-app-dodo-payments test suite.

The services import `config.config.get_settings()` at module-import time,
which would normally fail on a fresh checkout because the real settings
require MySQL + Dodo API credentials. The fixtures below monkey-patch
``config.config.get_settings`` for the duration of any test that needs it.
"""
import os
import sys
from pathlib import Path

# Make the repo root importable so `from services...` works without -e install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Provide a safety net for tests that import config without first patching it.
# Real test cases override settings via monkeypatch; this default just keeps
# the import from blowing up if the test forgot to set DODO_API_KEY.
if not os.environ.get("DODO_API_KEY"):
    os.environ.setdefault("DODO_API_KEY", "test-dodo-key")
if not os.environ.get("MYSQL_HOST"):
    os.environ.setdefault("MYSQL_HOST", "localhost")
if not os.environ.get("MYSQL_USER"):
    os.environ.setdefault("MYSQL_USER", "test")
if not os.environ.get("MYSQL_PASSWORD"):
    os.environ.setdefault("MYSQL_PASSWORD", "test")
if not os.environ.get("API_KEY_ENCRYPTION_KEY"):
    os.environ.setdefault("API_KEY_ENCRYPTION_KEY", "x" * 40)
if not os.environ.get("WEBHOOK_SIGNING_SECRET"):
    os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "whsec_dGVzdF9zZWNyZXRfa2V5MTIzNDU2Nzg=")


def pytest_configure(config):
    # Pytest arg sanity (kept here so contributors find it next to the test files).
    config.addinivalue_line(
        "markers",
        "unit: pure-Python unit tests (no DB, no network).",
    )
