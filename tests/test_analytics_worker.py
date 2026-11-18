"""Mixpanel event worker accounting.

Every queued event must be marked done even when its processing raises or
the Mixpanel client is unavailable -- otherwise shutdown()'s queue.join()
blocks forever after the first failed event.

Self-contained: sets required env before importing the app, no conftest.
"""
import os
import queue
import sys
import threading
from types import SimpleNamespace

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

import utils.analytics as analytics


class FailingClient:
    def track(self, *args, **kwargs):
        raise RuntimeError("boom")

    def people_set(self, *args, **kwargs):
        raise RuntimeError("boom")

    def people_increment(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(analytics, "_event_queue", queue.Queue())
    monkeypatch.setattr(analytics, "_worker_thread", None)
    monkeypatch.setattr(analytics, "_mixpanel_client", None)
    monkeypatch.setattr(
        analytics,
        "settings",
        SimpleNamespace(
            MIXPANEL_ENABLED=True,
            ENVIRONMENT="test",
            MIXPANEL_DATA_RESIDENCY=None,
            MIXPANEL_PROJECT_TOKEN="token",
        ),
    )
    yield


def _track_event():
    return {"type": "track", "distinct_id": "u1", "event_name": "E", "properties": {}}


def test_task_done_called_when_processing_raises(monkeypatch):
    monkeypatch.setattr(analytics, "_get_mixpanel_client", lambda: FailingClient())
    analytics._event_queue.put(_track_event())
    worker = threading.Thread(target=analytics._event_worker, daemon=True)
    worker.start()
    analytics._event_queue.put(None)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert analytics._event_queue.unfinished_tasks == 0


def test_task_done_called_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(analytics, "_get_mixpanel_client", lambda: None)
    analytics._event_queue.put(_track_event())
    worker = threading.Thread(target=analytics._event_worker, daemon=True)
    worker.start()
    analytics._event_queue.put(None)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert analytics._event_queue.unfinished_tasks == 0


def test_shutdown_returns_after_failed_event(monkeypatch):
    monkeypatch.setattr(analytics, "_get_mixpanel_client", lambda: FailingClient())
    analytics._event_queue.put(_track_event())
    worker = threading.Thread(target=analytics._event_worker, daemon=True)
    worker.start()
    monkeypatch.setattr(analytics, "_worker_thread", worker)

    shutdown = threading.Thread(target=analytics.shutdown, daemon=True)
    shutdown.start()
    shutdown.join(timeout=2)
    assert not shutdown.is_alive(), "shutdown() hung in queue.join() after a failed event"


def test_track_event_does_not_mutate_caller_properties(monkeypatch):
    monkeypatch.setattr(analytics, "_start_worker", lambda: None)
    props = {"amount": 1}
    analytics.track_event("u1", "E", props)
    assert props == {"amount": 1}
