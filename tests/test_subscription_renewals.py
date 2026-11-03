"""subscription.renewed fires on every billing cycle -- each renewal must land.

Dodo docs (docs.dodopayments.com, Subscription Webhooks): "subscription.renewed
| A subscription is renewed for the next billing period. The payload includes
payment_id, the ID of the renewal payment." The old idempotency check keyed on
(subscription_id, app_name) and returned early when *any* payment existed for
the subscription, so every renewal after the first was silently dropped: no
Payment row, no revenue record, no callback to the app.
"""
import asyncio

import pytest

from routers.payments import _handle_subscription_renewed
from services.database import AppRegistry, Payment, ProductCatalog


def renewal_payload(payment_id="pay_renew_1", created_at="2025-01-01T00:00:00Z"):
    data = {
        "subscription_id": "sub_123",
        "created_at": created_at,
        "expires_at": "2025-02-01T00:00:00Z",
        "recurring_pre_tax_amount": 2999,
        "currency": "USD",
        "customer": {"email": "customer@example.com"},
        "metadata": {
            "app_name": "demo-app",
            "user_id": "user-1",
            "product_id": "credits_50",
        },
    }
    if payment_id is not None:
        data["payment_id"] = payment_id
    return {"type": "subscription.renewed", "data": data}


@pytest.fixture(autouse=True)
def _seed_catalog(db_session):
    db_session.add(AppRegistry(
        app_name="demo-app",
        app_display_name="Demo",
        app_secret_key="sk_test",
    ))
    db_session.add(ProductCatalog(
        app_name="demo-app",
        product_id="credits_50",
        product_name="50 Credits",
        product_type="SUBSCRIPTION",
        price=29.99,
        currency="USD",
    ))
    db_session.commit()


def _payments(db_session):
    return db_session.query(Payment).order_by(Payment.id).all()


def test_every_renewal_is_recorded(db_session):
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r1"), "evt_1"))
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r2"), "evt_2"))
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r3"), "evt_3"))

    rows = _payments(db_session)
    assert len(rows) == 3, "renewals 2 and 3 must not be dropped as duplicates"
    assert [r.payment_id for r in rows] == ["pay_r1", "pay_r2", "pay_r3"]
    for row in rows:
        assert row.subscription_id == "sub_123"
        assert float(row.amount) == 29.99
        assert row.status.value == "SUCCESS"


def test_retried_delivery_is_not_duplicated(db_session):
    # Svix reuses webhook-id across retries of the same delivery.
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r1"), "evt_1"))
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r1"), "evt_1"))

    assert len(_payments(db_session)) == 1


def test_missing_payment_id_falls_back_to_event_id(db_session):
    # Dodo: payment_id is null "in the rare case that the renewal payment
    # can't be matched". The webhook-id is unique per message and stable
    # across retries, so it dedupes without dropping distinct renewals.
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id=None), "evt_1"))
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id=None), "evt_2"))
    assert len(_payments(db_session)) == 2

    # A retry of evt_1 is still one record.
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id=None), "evt_1"))
    rows = _payments(db_session)
    assert len(rows) == 2
    assert rows[0].payment_id == "sub_pay_evt_1"
    assert rows[1].payment_id == "sub_pay_evt_2"


def test_renewal_amount_is_cents_to_dollars(db_session):
    asyncio.run(_handle_subscription_renewed(db_session, renewal_payload(payment_id="pay_r1"), "evt_1"))
    row = _payments(db_session)[0]
    assert float(row.amount) == 29.99
    assert row.subscription_start is not None
    assert row.subscription_end is not None
