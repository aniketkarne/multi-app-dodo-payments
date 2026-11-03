"""MySQL credentials with URL-special characters must survive the database URL.

The URL was built by string concatenation, so a password containing `@`, `:`,
`/`, `#` or `%` (all legal in MySQL passwords, all common in generated ones)
produced a URL that SQLAlchemy parsed into the wrong host/username/password --
and every connection then failed with an opaque "Unknown database" or access
denied error. Build the URL through SQLAlchemy's URL object instead.
"""
from sqlalchemy.engine import make_url

from config.config import Settings


def _settings(**kwargs):
    defaults = {
        "DODO_API_KEY": "test-key",
        "MYSQL_HOST": "db.example.com",
        "MYSQL_USER": "test_user",
        "MYSQL_PASSWORD": "test_password",
        "MYSQL_DATABASE": "dodo_payments_db",
        "API_KEY_ENCRYPTION_KEY": "test-encryption-key-min-32-characters",
        "WEBHOOK_SIGNING_SECRET": "test-webhook-signing-secret",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_plain_credentials_still_parse():
    url = make_url(_settings().database_url)
    assert url.drivername == "mysql+pymysql"
    assert url.username == "test_user"
    assert url.password == "test_password"
    assert url.host == "db.example.com"
    assert url.port == 3306
    assert url.database == "dodo_payments_db"


def test_special_character_password_round_trips():
    password = "p@ss:w/rd#1%"
    url = make_url(_settings(MYSQL_PASSWORD=password).database_url)
    assert url.password == password
    assert url.host == "db.example.com"
    assert url.database == "dodo_payments_db"


def test_special_character_username_round_trips():
    username = "app@tenant/1"
    url = make_url(_settings(MYSQL_USER=username).database_url)
    assert url.username == username
    assert url.password == "test_password"


def test_custom_port_and_database_round_trip():
    url = make_url(_settings(MYSQL_PORT=3307, MYSQL_DATABASE="pay db").database_url)
    assert url.port == 3307
    assert url.database == "pay db"
