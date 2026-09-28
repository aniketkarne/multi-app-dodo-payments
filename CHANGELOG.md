# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `services/credit_service.CreditService.refund_credits`: when the requested refund
  exceeded the user's balance, the `credit_transactions` row stored the *requested*
  amount while the balance was clamped to 0, producing audit-log rows where
  `balance_before + amount != balance_after`. The row now records the *applied*
  amount so the invariant always holds. A new `clamped` flag (and WARNING log)
  surfaces over-refund attempts for ops visibility.
- `services/dodo_service`: `to_cents()` is now a real helper. Replaces
  `int(amount * 100)` (which silently lost 1¢ on `29.99`, `9.99`, `0.10` etc.)
  with a `Decimal`-based conversion using `ROUND_HALF_UP`. Applied at both
  `create_checkout_session` and `create_refund`.
- `services/dodo_service.DodoPaymentsService.verify_webhook_signature`: the full
  expected and received HMAC signatures are no longer logged at INFO. The previous
  behaviour shipped a forgery primitive into default log streams. Full sigs are
  now DEBUG-only; INFO and WARNING only log length + 8-char prefix.
- `refund_credits` now rejects non-positive amounts with `ValueError` instead of
  silently logging a no-op transaction.

### Added
- `tests/test_refund_credits.py` — 6 regression tests pinning the
  `balance_before + amount == balance_after` invariant and the WARNING-on-clamp
  contract.
- `tests/test_to_cents.py` — 12 regression tests covering the float-trap
  amounts (`29.99`, `9.99`, `0.10`, `19.99`, `1.005`), accepted input types,
  `None` round-trip, rounding mode, and garbage-input rejection.
- `tests/test_webhook_signature_logging.py` — 3 regression tests asserting no
  full HMAC signature appears at INFO or WARNING, and only an 8-char prefix at
  DEBUG.
- `tests/conftest.py` — makes the repo root importable so tests run without
  `pip install -e .`; sets a safety-net env so a forgotten `DODO_API_KEY`
  doesn't crash collection.
- `.github/workflows/tests.yml` — runs `pytest tests/ -v` on push and PR against
  Python 3.11 on ubuntu-latest. Installs `dodopayments`, `pytest`,
  `pytest-asyncio` only — no MySQL, no Dodo creds required.
