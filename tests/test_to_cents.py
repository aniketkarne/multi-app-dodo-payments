"""
Regression tests for `services.dodo_service.to_cents`.

Background: the previous implementation used ``int(amount * 100)`` which
silently truncates floating-point precision errors. Examples:
    int(29.99 * 100) == 2998   # should be 2999
    int(0.10 * 100)  == 9      # borderline
    int(9.99 * 100)  == 998    # should be 999

These tests lock the exact-cents contract — Dodo charges the integer we send,
so an off-by-one cent on every `29.99` product is a real money bug.
"""
from decimal import Decimal

import pytest

from services.dodo_service import to_cents


@pytest.mark.unit
class TestToCentsFloatTrapRegression:
    """Floats that bit the old ``int(amount * 100)`` implementation."""

    def test_29_99_does_not_lose_one_cent(self):
        # The classic float-trap amount. Pre-fix: 2998. Post-fix: 2999.
        assert to_cents(29.99) == 2999

    def test_9_99_does_not_lose_one_cent(self):
        # Same family, smaller magnitude.
        assert to_cents(9.99) == 999

    def test_0_1_does_not_lose_to_float_repr(self):
        # `0.1` has the exact binary rep 0.00011001100...
        # `int(0.10 * 100)` historically evaluates to 9.
        assert to_cents(0.10) == 10

    def test_0_1_as_float_is_still_ten_cents(self):
        # Same number as a bare float.
        assert to_cents(0.1) == 10

    def test_19_99_does_not_lose_one_cent(self):
        assert to_cents(19.99) == 1999


@pytest.mark.unit
class TestToCentsAcceptedTypes:
    """The helper accepts the shapes it sees in the codebase."""

    def test_decimal_passes_through(self):
        assert to_cents(Decimal("29.99")) == 2999

    def test_int_passes_through(self):
        assert to_cents(5) == 500

    def test_string_passes_through(self):
        assert to_cents("2.99") == 299

    def test_none_returns_none_for_full_refund(self):
        # None means "full refund" in the SDK call, must round-trip as None.
        assert to_cents(None) is None


@pytest.mark.unit
class TestToCentsRounding:
    """Banker's / half-up rounding semantics on weird third-decimal inputs."""

    def test_rounds_half_up(self):
        # 1.005 -> 101 cents under ROUND_HALF_UP
        assert to_cents(1.005) == 101

    def test_rounds_half_away_from_zero_for_negative_amounts(self):
        # Negative not used in practice but shouldn't silently explode.
        assert to_cents(-1.50) == -150


@pytest.mark.unit
class TestToCentsValidation:
    """Garbage input should raise rather than silently returning 0."""

    def test_garbage_string_raises(self):
        with pytest.raises(ValueError):
            to_cents("not-a-number")

    def test_unparseable_input_raises(self):
        with pytest.raises(ValueError):
            to_cents("not-a-number-either")
