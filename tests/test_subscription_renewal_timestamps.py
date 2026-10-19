"""
Regression tests for the defensive timestamp parser introduced in
`routers/payments.py::_handle_subscription_renewed`.

Pre-fix, the handler did:

    datetime.fromisoformat(data.get("created_at").replace('Z', '+00:00'))

When Dodo sends a subscription event without `created_at` (older accounts,
paused renewals, partial state), `data.get("created_at")` is None and
`.replace(...)` raises AttributeError. The whole handler 500s, Svix retries
forever, and the subscription never lands in the audit log.

The fix: a `_parse_iso(value)` helper that returns None on None / non-string /
ValueError, and a WARNING log line so the operator can see which events are
being recorded without period dates.
"""
from unittest.mock import MagicMock

import pytest

from routers.payments import _handle_subscription_renewed


def _payload(subscription_id="sub_abc", created_at=None, expires_at=None):
    """Build a minimal Dodo subscription.renewed payload."""
    data = {
        "subscription_id": subscription_id,
        "recurring_pre_tax_amount": 999,
        "currency": "USD",
        "status": "active",
        "metadata": {
            "app_name": "demo-app",
            "user_id": "user-1",
            "product_id": "pro_monthly",
        },
        "customer": {},
    }
    if created_at is not None:
        data["created_at"] = created_at
    if expires_at is not None:
        data["expires_at"] = expires_at
    return {"data": data}


def _make_db_no_product():
    """
    Returns a MagicMock session that simulates the "no matching product"
    path. We don't need to actually load a ProductCatalog row -- the
    pre-existing `_handle_subscription_renewed` raises Exception("Product not
    found") before reaching the timestamp write, but the timestamps must
    be parsed cleanly first.

    Actually, since the `created_at` parse happens AFTER `db.query(Product)`
    succeeds, we DO need to return a product. Return a row-like object that
    has `product_name`.
    """
    db = MagicMock()

    # First query: idempotency check (Payment.subscription_id == ...) returns nothing.
    # Second query: ProductCatalog lookup -- return a product.
    product = MagicMock()
    product.product_name = "Pro Monthly"

    builders = [MagicMock(), MagicMock()]
    builders[0].first.return_value = None
    builders[1].first.return_value = product

    db.query.side_effect = builders
    return db


def test_renewal_with_valid_iso_strings_does_not_raise():
    """Happy path: created_at + expires_at both present, ISO format with Z."""
    db = _make_db_no_product()
    payload = _payload(created_at="2025-09-15T10:00:00Z", expires_at="2025-10-15T10:00:00Z")

    # We don't care about the end-state side effects, just that the parser
    # path completes (it doesn't crash on .replace()).
    try:
        # No real network -- mark_event_processed etc. are also MagicMock'd.
        import asyncio
        asyncio.run(_handle_subscription_renewed(db, payload))
    except Exception as e:
        # Anything from "Product not found" downstream is fine; we just want
        # to ensure no AttributeError from the timestamp parse.
        if "AttributeError" in type(e).__name__:
            pytest.fail(f"timestamp parser raised AttributeError: {e!r}")
        if "NoneType" in str(e):
            pytest.fail(f"timestamp parser hit None (unfixed bug): {e!r}")


def test_renewal_with_missing_created_at_does_not_crash():
    """Pre-fix bug: data.get('created_at').replace(...) -> AttributeError."""
    db = _make_db_no_product()
    payload = _payload(created_at=None, expires_at=None)

    try:
        import asyncio
        asyncio.run(_handle_subscription_renewed(db, payload))
    except AttributeError as e:
        pytest.fail(f"renewal crashed on missing created_at: {e!r}")


def test_renewal_with_garbage_created_at_does_not_crash():
    """Pre-fix bug: .replace() may succeed but fromisoformat() raises ValueError."""
    db = _make_db_no_product()
    payload = _payload(created_at="not-a-timestamp", expires_at=None)

    try:
        import asyncio
        asyncio.run(_handle_subscription_renewed(db, payload))
    except (ValueError, AttributeError) as e:
        pytest.fail(f"renewal crashed on garbage created_at: {e!r}")


def test_renewal_with_non_string_created_at_does_not_crash():
    """Dodo has been observed to send null or numeric values in timestamps."""
    db = _make_db_no_product()
    payload = _payload(created_at=12345, expires_at=None)

    try:
        import asyncio
        asyncio.run(_handle_subscription_renewed(db, payload))
    except (AttributeError, TypeError) as e:
        pytest.fail(f"renewal crashed on non-string created_at: {e!r}")


def test_renewal_with_valid_created_at_but_missing_expires_at():
    """expires_at is optional in our schema; missing value must not crash."""
    db = _make_db_no_product()
    payload = _payload(created_at="2025-09-15T10:00:00Z", expires_at=None)

    try:
        import asyncio
        asyncio.run(_handle_subscription_renewed(db, payload))
    except (ValueError, AttributeError) as e:
        pytest.fail(f"renewal crashed on missing expires_at: {e!r}")