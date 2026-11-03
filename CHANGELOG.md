# Changelog

## Unreleased

### Fixed

- **App callback signatures never matched the wire.** The signature was computed
  over `json.dumps(payload, sort_keys=True)` while the request body went out
  through httpx's `json=` serialization (insertion order) — a different byte
  string. Any receiver that HMACs the raw request body (the standard webhook
  verification in every language and every webhook library) rejected every
  callback. The body is now serialized once, sent as those exact bytes, and
  signed over: hex HMAC-SHA256 keyed with `WEBHOOK_SIGNING_SECRET`.
- **Every `subscription.renewed` after the first was silently dropped.** Dodo
  fires the event on each billing cycle (payload `payment_id` = that cycle's
  renewal payment), but the idempotency check keyed on
  `(subscription_id, app_name)` and returned early once *any* payment existed
  for the subscription — so renewals 2..N never produced a Payment row, a
  revenue record, or an app callback. Idempotency now keys on the renewal
  `payment_id`, falling back to the stable `webhook-id` when Dodo omits it
  ("in the rare case that the renewal payment can't be matched").
- **`Settings.database_url` broke on credentials with URL-special characters.**
  The MySQL URL was built by string concatenation, so a password containing
  `@ : / # %` produced a URL SQLAlchemy parsed into the wrong
  host/username/password and every connection failed with an opaque error.
  The URL is now built through SQLAlchemy's `URL.create`, which escapes both
  fields.

### Added

- `tests/` — 12 tests covering callback signature/serialization (raw-body and
  README-style verification both pass), per-renewal idempotency and the
  webhook-id fallback, and database URL escaping. SQLite-compatible primary
  keys (`BigInteger().with_variant(Integer, "sqlite")`) so the suite runs
  without a MySQL server — the MySQL DDL is unchanged.
- `.github/workflows/tests.yml` — runs the suite on Python 3.11 on every
  push/PR. Needs no secrets.
