"""Shared test fixtures.

The service reads required settings at import time and every app module pulls
in `services.database`, so the environment has to be populated before any of
them are imported. This file runs before the test modules for that reason.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DODO_API_KEY", "test-dodo-key")
os.environ.setdefault("DODO_BASE_URL", "https://test.dodopayments.com")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test_user")
os.environ.setdefault("MYSQL_PASSWORD", "test_password")
os.environ.setdefault("MYSQL_DATABASE", "test_db")
os.environ.setdefault("API_KEY_ENCRYPTION_KEY", "test-encryption-key-min-32-characters")
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "test-webhook-signing-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.database import Base


@pytest.fixture()
def db_session():
    """In-memory SQLite session with all service tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()
