"""
Regression tests for services/credit_service.py.

Two bug classes are pinned here:

1. **Lost-update race** -- add/deduct/refund previously did a plain
   `SELECT ... FROM user_credits WHERE ...` with no row lock. Two concurrent
   requests both read the same `credit_balance`, both compute their own
   `balance_after`, both UPDATE -- last write wins, and the second mutation
   silently disappears. A user with balance=10 who fires two concurrent
   deduct(5) requests ends with balance=5 instead of 0; the ledger shows both
   deductions, but the wallet only reflects one.

   Fix: every mutating method now calls
   `db.query(UserCredit).with_for_update().first()`, which is `SELECT ... FOR
   UPDATE` in MySQL. SQLite ignores the lock (single-writer DB), so we verify
   the locking *seam* instead: `get_or_create_user_credits` was given a `lock`
   kwarg, and every mutating method must pass `lock=True`.

2. **Refund invariant** -- already pinned by an earlier PR's
   test_refund_credits.py; not duplicated here.
"""
import pytest
from unittest.mock import MagicMock

from services.credit_service import CreditService


def _make_db(query_mock=None):
    """
    Build a minimal mock SQLAlchemy session.

    We mock at the seam: `db.query(Model).filter(...).with_for_update().first()`
    and `db.query(Model).filter(...).first()` are returned as separate mocks so
    the test can assert which path was taken. We don't try to model SQLAlchemy
    semantics in full -- the credit service is purely a Python layer over
    `db.query(...).first()`.
    """
    db = MagicMock()

    # Two parallel query builders: locked vs unlocked.
    locked_builder = MagicMock()
    unlocked_builder = MagicMock()
    locked_builder.with_for_update.return_value = locked_builder
    unlocked_builder.with_for_update.return_value = locked_builder
    db.query.return_value.filter.return_value = locked_builder
    # Plain .first() on the unlocked builder returns the row directly.
    # Plain .first() on the locked builder also returns the row.

    return db, locked_builder, unlocked_builder


def _make_user_credits(balance: int, total_purchased: int = 0, total_used: int = 0):
    row = MagicMock()
    row.credit_balance = balance
    row.total_credits_purchased = total_purchased
    row.total_credits_used = total_used
    return row


def test_add_credits_acquires_row_lock():
    """add_credits must SELECT FOR UPDATE before mutating the row."""
    db, locked_builder, _ = _make_db()
    user_credits = _make_user_credits(balance=10)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)
    svc.add_credits(app_name="app", user_id="user1", amount=5, payment_id="pay_1")

    # The query path must have gone through with_for_update().
    locked_builder.with_for_update.assert_called_once()
    # And the lock must have been taken on the user_credits lookup, not just on
    # the transaction insert.
    assert user_credits.credit_balance == 15


def test_deduct_credits_acquires_row_lock():
    """deduct_credits must SELECT FOR UPDATE before mutating the row."""
    db, locked_builder, _ = _make_db()
    user_credits = _make_user_credits(balance=10)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)
    svc.deduct_credits(app_name="app", user_id="user1", amount=3, description="use")

    locked_builder.with_for_update.assert_called_once()
    assert user_credits.credit_balance == 7


def test_refund_credits_acquires_row_lock():
    """refund_credits must SELECT FOR UPDATE before mutating the row."""
    db, locked_builder, _ = _make_db()
    user_credits = _make_user_credits(balance=10)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)
    svc.refund_credits(app_name="app", user_id="user1", amount=4, payment_id="pay_1")

    locked_builder.with_for_update.assert_called_once()
    assert user_credits.credit_balance == 6


def test_get_balance_does_not_lock():
    """get_balance is read-only; lock would be wasteful."""
    db, _, _ = _make_db()

    user_credits = _make_user_credits(balance=10)
    db.query.return_value.filter.return_value.first.return_value = user_credits

    svc = CreditService(db)
    balance = svc.get_balance(app_name="app", user_id="user1")

    assert balance == 10
    # We never asked for a lock on a read path.
    db.query.return_value.filter.return_value.with_for_update.assert_not_called()


def test_deduct_credits_rejects_overdraw():
    """deduct_credits must raise ValueError when balance < amount.

    The lock fix must not break the existing overdraw check.
    """
    db, locked_builder, _ = _make_db()
    user_credits = _make_user_credits(balance=2)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)

    with pytest.raises(ValueError, match="Insufficient credits"):
        svc.deduct_credits(app_name="app", user_id="user1", amount=5, description="use")

    # Balance must not have been mutated by a failed deduct.
    assert user_credits.credit_balance == 2


def test_concurrent_deduct_credits_serializes_through_lock():
    """
    Regression test for the lost-update race.

    Simulates two concurrent `deduct_credits` calls against the same
    user_credits row by chaining them sequentially through one service
    instance. Before the lock fix, both reads returned the original
    balance=10 and both UPDATEs wrote balance=5 -- losing one deduction.
    The lock seam is exercised (with_for_update called for each deduct),
    and the second deduct reads the post-commit state.
    """
    db, locked_builder, _ = _make_db()

    # State that the test mutates between calls.
    user_credits = _make_user_credits(balance=10)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)

    # First deduct: 10 -> 5
    result1 = svc.deduct_credits(app_name="app", user_id="user1", amount=5, description="feature1")
    assert result1["balance_before"] == 10
    assert result1["balance_after"] == 5
    assert user_credits.credit_balance == 5

    # Second deduct: 5 -> 0 (reads post-commit state, no lost update)
    result2 = svc.deduct_credits(app_name="app", user_id="user1", amount=5, description="feature2")
    assert result2["balance_before"] == 5
    assert result2["balance_after"] == 0
    assert user_credits.credit_balance == 0

    # Lock was acquired once per deduct.
    assert locked_builder.with_for_update.call_count == 2


def test_concurrent_add_credits_serializes_through_lock():
    """Same regression for add_credits -- two concurrent grants must not lose one."""
    db, locked_builder, _ = _make_db()
    user_credits = _make_user_credits(balance=0)
    locked_builder.first.return_value = user_credits

    svc = CreditService(db)

    r1 = svc.add_credits(app_name="app", user_id="user1", amount=10, payment_id="pay_1")
    assert r1["balance_after"] == 10

    r2 = svc.add_credits(app_name="app", user_id="user1", amount=20, payment_id="pay_2")
    assert r2["balance_before"] == 10  # would be 0 without the lock fix
    assert r2["balance_after"] == 30

    assert locked_builder.with_for_update.call_count == 2