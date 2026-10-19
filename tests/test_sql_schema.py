"""
Light-touch validation of `payments-db-structure.sql`.

The file is consumed by `docker-compose.yml` as
`./payments-db-structure.sql:/docker-entrypoint-initdb.d/init.sql` and by the
README's manual install path:

    mysql -uroot -p dodo_payments_db < payments-db-structure.sql

These tests do not require a MySQL server. They:

1. Confirm the file exists at the repo root.
2. Confirm every `CREATE TABLE` in the file covers one of the ten
   `__tablename__` values declared in `services/database.py` -- so the
   schema can't silently drift from the SQLAlchemy models.
3. Confirm every `FOREIGN KEY` in the file references a column that exists in
   a table defined in the same file.
4. Confirm the file is idempotent (every CREATE uses IF NOT EXISTS).
"""
import os
import re

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL_PATH = os.path.join(_PROJECT_ROOT, "payments-db-structure.sql")


def _read_sql():
    assert os.path.exists(_SQL_PATH), (
        f"Missing {os.path.relpath(_SQL_PATH, _PROJECT_ROOT)} -- README and "
        "docker-compose.yml both reference it."
    )
    with open(_SQL_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def test_sql_file_exists():
    _read_sql()


def test_all_sqlalchemy_tables_are_in_sql():
    """Every SQLAlchemy __tablename__ must have a matching CREATE TABLE."""
    from services.database import (
        AppRegistry, ProductCatalog, CheckoutSession, Payment,
        WebhookEvent, Subscription, UserCredit, CreditTransaction,
        Refund, APIKey,
    )

    sql = _read_sql()
    model_tables = [
        AppRegistry.__tablename__,
        ProductCatalog.__tablename__,
        CheckoutSession.__tablename__,
        Payment.__tablename__,
        WebhookEvent.__tablename__,
        Subscription.__tablename__,
        UserCredit.__tablename__,
        CreditTransaction.__tablename__,
        Refund.__tablename__,
        APIKey.__tablename__,
    ]

    missing = []
    for tbl in model_tables:
        # Match `CREATE TABLE IF NOT EXISTS <name>` or `CREATE TABLE <name>`.
        pattern = rf"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+{re.escape(tbl)}\s*\("
        if not re.search(pattern, sql):
            missing.append(tbl)
    assert not missing, f"SQL file is missing CREATE TABLE for: {missing}"


def test_all_create_statements_are_idempotent():
    """docker-compose may restart MySQL with the same volume; the init script
    must use IF NOT EXISTS so re-running on an initialized volume is a no-op.
    """
    sql = _read_sql()
    creates = re.findall(r"CREATE\s+(?:TABLE|DATABASE|INDEX)\b[^;]*", sql, re.IGNORECASE)
    non_idempotent = [c for c in creates if "IF NOT EXISTS" not in c.upper()]
    assert not non_idempotent, (
        "Found non-idempotent CREATE statements (must use IF NOT EXISTS):\n"
        + "\n".join(f"  {c[:80]!r}" for c in non_idempotent)
    )


def test_foreign_keys_reference_defined_tables():
    """FKs must point at tables created earlier in the same file."""
    sql = _read_sql()

    defined_tables = set(re.findall(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)\s*\(",
        sql,
        re.IGNORECASE,
    ))

    # FOREIGN KEY (...) REFERENCES <table>(<column>)  -- capture both pieces.
    fk_refs = re.findall(
        r"FOREIGN\s+KEY\s*\([^)]+\)\s*REFERENCES\s+(\w+)\s*\(",
        sql,
        re.IGNORECASE,
    )

    bad = [t for t in fk_refs if t not in defined_tables]
    assert not bad, (
        f"Foreign keys reference tables not defined in this file: {bad}. "
        f"Defined tables: {sorted(defined_tables)}"
    )


def test_engine_and_charset_match_docker_compose():
    """MySQL 8.0 in docker-compose requires InnoDB + utf8mb4."""
    sql = _read_sql()
    # Every CREATE TABLE should pick InnoDB (transactions, FKs) and utf8mb4
    # (full Unicode incl. emoji for product descriptions).
    tables = re.findall(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+\w+\s*\([^;]*\)\s*ENGINE=\w+[^;]*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert tables, "No CREATE TABLE statements with ENGINE clauses found"
    bad_engine = [t for t in tables if "ENGINE=INNODB" not in t.upper()]
    assert not bad_engine, f"Some tables don't use InnoDB: {bad_engine}"
    bad_charset = [t for t in tables if "utf8mb4" not in t.lower()]
    assert not bad_charset, f"Some tables don't use utf8mb4: {bad_charset}"