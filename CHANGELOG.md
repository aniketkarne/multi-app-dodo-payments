# Changelog

All notable changes to this project are documented in this file. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- **Credit service lost-update race.** `CreditService.add_credits`,
  `deduct_credits`, and `refund_credits` previously did a plain
  `SELECT ... FROM user_credits WHERE ...` with no row lock. Two concurrent
  requests both read the same `credit_balance`, both computed their own
  `balance_after`, and both `UPDATE`d -- last write wins, the second mutation
  silently disappears. A user with balance=10 firing two simultaneous
  deduct(5) calls ended with balance=5 instead of 0, with the ledger showing
  both deductions but the wallet only reflecting one. Every mutating method
  now acquires a row-level lock via `with_for_update()`, so concurrent
  add/deduct/refund calls serialize at the DB.
- **Subscription renewal crash on missing timestamps.**
  `_handle_subscription_renewed` did
  `datetime.fromisoformat(data.get("created_at").replace('Z', '+00:00'))`,
  which raises `AttributeError: 'NoneType' object has no attribute 'replace'`
  when Dodo sends a subscription event without `created_at` (older accounts,
  paused renewals, partial state). The whole handler 500'd, Svix retried
  indefinitely, and the subscription never landed in the audit log. A new
  `_parse_iso(value)` helper returns `None` on `None` / non-string /
  `ValueError`; the handler now records the renewal with `subscription_start
  = None` and emits a WARNING log so operators can see which events were
  recorded without period dates.
- **`payments-db-structure.sql` was missing from the repo.** Both
  `README.md` (Docker setup step 4) and `docker-compose.yml`
  (`./payments-db-structure.sql:/docker-entrypoint-initdb.d/init.sql`)
  referenced the file, but it had never landed on `main`. New users running
  the documented MySQL init path had no schema to apply. The file is now
  restored and mirrors the SQLAlchemy models in `services/database.py`
  (10 tables, all FKs, all indexes); every `CREATE` uses `IF NOT EXISTS` so
  re-running against an already-initialized volume is a no-op.

### Changed
- **`README.md` Docker setup** dropped the manual `docker exec ... <
  payments-db-structure.sql` step. The MySQL container auto-loads the file
  via the volume mount on first startup; the manual step was a duplicate of
  what the compose file already does and was a no-op once the SQL file
  exists.

### Added
- `payments-db-structure.sql` -- canonical MySQL DDL mirroring the SQLAlchemy
  models. Idempotent (`IF NOT EXISTS` everywhere), InnoDB, utf8mb4.
- `tests/test_credit_service_locks.py` -- 7 tests pinning the row-lock
  contract for `CreditService`.
- `tests/test_subscription_renewal_timestamps.py` -- 5 tests pinning the
  defensive timestamp parser.
- `tests/test_sql_schema.py` -- 5 tests pinning the SQL file's table
  coverage, idempotency, FK validity, and engine/charset.
- `.github/workflows/tests.yml` -- ubuntu-latest, Python 3.11, runs pytest
  + ruff on every push/PR.