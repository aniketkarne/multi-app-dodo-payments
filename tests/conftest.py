"""
Shared pytest fixtures for the Dodo Payments Microservice test suite.

The app's real DB layer uses MySQL via `services.database.engine`, which
requires a running MySQL container. None of the regression tests in this
suite touch the DB directly -- they exercise pure logic in
`services/credit_service.py`, `routers/payments.py`, and the SQL DDL in
`payments-db-structure.sql`. We mock out the DB session where needed via
SQLAlchemy's in-memory SQLite.
"""
import os
import sys

# Make project root importable as `services.*`, `routers.*`, etc.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Provide a minimal .env so pydantic-settings can instantiate Settings() during
# tests that import config.config. Tests that touch settings don't connect to the
# network -- they just read local defaults.
os.environ.setdefault("DODO_API_KEY", "test_dodo_api_key")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "root")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "dodo_payments_db")
os.environ.setdefault("API_KEY_ENCRYPTION_KEY", "test_encryption_key_min_32_characters_long")
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "test_webhook_signing_secret")
os.environ.setdefault("MIXPANEL_ENABLED", "false")