"""
Regression tests for the audit-log invariant in
``services.credit_service.CreditService.refund_credits``.

The previous implementation wrote ``amount=-requested_amount`` into the
CreditTransaction row while clamping ``balance_after`` via
``max(0, balance_before - requested_amount)``. When the requested refund
exceeded the user's balance, this produced a row whose arithmetic does
**not** reconcile::

    balance_before + amount != balance_after

For example, balance=5, refund=10 -> transaction logged
`amount=-10, balance_before=5, balance_after=0`, which reads as the user
being charged 10 from a balance of 5 to land at 0, but actually only 5
was refunded.

Post-fix, the row stores the *applied* amount, and the invariant
``balance_before + amount == balance_after`` always holds.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest


# --- Minimal in-memory stand-ins for SQLAlchemy ORM -----------------
@dataclass
class FakeUserCredit:
    app_name: str
    user_id: str
    credit_balance: int = 0
    total_credits_purchased: int = 0
    total_credits_used: int = 0


@dataclass
class FakeTransaction:
    transaction_id: str = ""
    app_name: str = ""
    user_id: str = ""
    type: Any = None
    amount: int = 0
    balance_before: int = 0
    balance_after: int = 0
    payment_id: Optional[str] = None
    description: Optional[str] = None


class FakeQuery:
    """Generic SQLAlchemy-shaped query supporting ``filter().first()``."""

    def __init__(self, session, target_attr_pairs):
        # target_attr_pairs is a list of (col_name, expected_value) tuples
        # derived from filter() calls.
        self._session = session
        self._target_attr_pairs = target_attr_pairs

    def filter(self, *clauses):
        pairs = list(self._target_attr_pairs)
        for clause in clauses:
            pairs.append(_extract_pair(clause))
        return FakeQuery(self._session, pairs)

    def first(self):
        # First match wins. iterate over rows; track which model
        # we should look at by the col_name list.
        col_names = {p[0] for p in self._target_attr_pairs}
        if col_names <= {"app_name", "user_id"} or col_names <= {"app_name"}:
            rows = list(self._session.users.values())
            return _first_match(rows, self._target_attr_pairs)
        if "type" in col_names:
            return _first_match(self._session.transactions, self._target_attr_pairs)
        return _first_match(self._session.users.values(), self._target_attr_pairs)

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._session.users.values())


def _extract_pair(clause):
    """Parse ``Table.col == value`` -> (col_name, value)."""
    left = getattr(clause, "left", None)
    right = getattr(clause, "right", None)
    col_name = getattr(left, "key", None) or getattr(left, "name", None)
    value = getattr(right, "value", right) if right is not None else None
    return (col_name, value)


def _first_match(rows, target_attr_pairs):
    for row in rows:
        for col, value in target_attr_pairs:
            if col is None:
                continue
            if getattr(row, col, None) != value:
                break
        else:
            return row
    return None


@dataclass
class FakeSession:
    users: Dict[tuple, FakeUserCredit] = field(default_factory=dict)
    transactions: List[FakeTransaction] = field(default_factory=list)
    next_id: int = 0

    def query(self, _model):
        return FakeQuery(self, [])

    def add(self, obj):
        if hasattr(obj, "type") and not isinstance(getattr(obj, "type", None), type):
            # CreditTransaction-ish row (uses an enum/string, not a class).
            self.next_id += 1
            obj.transaction_id = obj.transaction_id or f"tx_{self.next_id:016d}"
            self.transactions.append(obj)
        elif hasattr(obj, "app_name") and hasattr(obj, "user_id"):
            # UserCredit-ish row.
            self.users[(obj.app_name, obj.user_id)] = obj
        else:
            raise AssertionError(f"FakeSession.add unexpected object: {obj!r}")

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def rollback(self):
        pass


# --- Tests ----------------------------------------------------------
@pytest.mark.unit
class TestRefundCreditsReconciliationInvariant:
    """balance_before + amount == balance_after, always."""

    def test_full_refund_from_zero_balance_is_a_no_op(self):
        # No pre-existing UserCredit row -> get_or_create_user_credits
        # will mint one. We just check the contract on the transaction.
        session = FakeSession()
        from services.credit_service import CreditService
        service = CreditService(session)
        result = service.refund_credits(
            app_name="app1", user_id="u1", amount=10, payment_id="pay_1"
        )
        assert result["balance_after"] == 0
        assert result["clamped"] is True
        assert result["amount"] == 0  # actual amount applied
        assert len(session.transactions) == 1
        tx = session.transactions[0]
        assert tx.amount == 0
        # Invariant: balance_before (0) + amount (0) == balance_after (0)
        assert tx.balance_before + tx.amount == tx.balance_after

    def test_partial_refund_normal_balance(self):
        session = FakeSession()
        session.users[("app1", "u1")] = FakeUserCredit(
            app_name="app1", user_id="u1", credit_balance=100
        )
        from services.credit_service import CreditService
        result = CreditService(session).refund_credits(
            app_name="app1", user_id="u1", amount=30, payment_id="pay_2"
        )
        assert result["balance_after"] == 70
        assert result["clamped"] is False
        tx = session.transactions[0]
        assert tx.amount == -30
        assert tx.balance_before + tx.amount == tx.balance_after

    def test_over_refund_clamps_and_logs_truthfully(self):
        """The bug the regression is for: refund > balance used to leave the
        transaction arithmetic unreconcilable. New behavior: log the actual
        amount applied so the invariant holds."""
        session = FakeSession()
        session.users[("app1", "u1")] = FakeUserCredit(
            app_name="app1", user_id="u1", credit_balance=5
        )
        from services.credit_service import CreditService
        result = CreditService(session).refund_credits(
            app_name="app1", user_id="u1", amount=10, payment_id="pay_3"
        )

        # New contract fields
        assert result["requested_amount"] == 10
        assert result["clamped"] is True
        assert result["amount"] == -5  # applied, not requested
        assert result["balance_before"] == 5
        assert result["balance_after"] == 0

        # Reconciliation invariant
        tx = session.transactions[0]
        assert tx.balance_before + tx.amount == tx.balance_after
        # == 5 + (-5) == 0
        assert tx.balance_before + tx.amount == 0

    def test_exact_refund_drains_balance_not_below(self):
        session = FakeSession()
        session.users[("app1", "u1")] = FakeUserCredit(
            app_name="app1", user_id="u1", credit_balance=42
        )
        from services.credit_service import CreditService
        result = CreditService(session).refund_credits(
            app_name="app1", user_id="u1", amount=42, payment_id="pay_4"
        )
        assert result["balance_after"] == 0
        assert result["clamped"] is False
        assert result["amount"] == -42
        tx = session.transactions[0]
        assert tx.balance_before + tx.amount == tx.balance_after

    def test_zero_or_negative_amount_rejected(self):
        session = FakeSession()
        session.users[("app1", "u1")] = FakeUserCredit(
            app_name="app1", user_id="u1", credit_balance=10
        )
        from services.credit_service import CreditService
        with pytest.raises(ValueError):
            CreditService(session).refund_credits(
                app_name="app1", user_id="u1", amount=0, payment_id="pay_5"
            )
        with pytest.raises(ValueError):
            CreditService(session).refund_credits(
                app_name="app1", user_id="u1", amount=-5, payment_id="pay_6"
            )


@pytest.mark.unit
class TestRefundCreditsClampLogging:
    """An over-refund is an audit-log concern: should produce a WARNING."""

    def test_over_refund_emits_warning(self, caplog):
        session = FakeSession()
        session.users[("app1", "u1")] = FakeUserCredit(
            app_name="app1", user_id="u1", credit_balance=5
        )
        from services.credit_service import CreditService

        with caplog.at_level(logging.WARNING, logger="services.credit_service"):
            CreditService(session).refund_credits(
                app_name="app1", user_id="u1", amount=20, payment_id="pay_7"
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "over-refund should have produced at least one WARNING"
        msg = warnings[0].getMessage()
        assert "clamped" in msg.lower()
