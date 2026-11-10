import psycopg
import pytest

from catalogue.db import DatabaseUnavailable, WrongRoleError, connect, require_tls, tls_in_use

SECRET = "s3cret-pw"
URL = f"postgresql://catalogue_importer.projref:{SECRET}@db.example.invalid:5432/postgres"


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_a_weak_sslmode_becomes_require(sslmode):
    result = require_tls(f"{URL}?sslmode={sslmode}")

    assert "sslmode=require" in result
    assert f"sslmode={sslmode}" not in result


def test_a_missing_sslmode_becomes_require():
    assert require_tls(URL).endswith("?sslmode=require")


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_a_strong_sslmode_is_kept(sslmode):
    url = f"{URL}?sslmode={sslmode}"

    assert require_tls(url) == url


def test_a_non_postgres_url_is_refused_without_echoing_it():
    with pytest.raises(ValueError) as error:
        require_tls(f"mysql://user:{SECRET}@host/db")

    assert SECRET not in str(error.value)


class FakeConnection:
    def __init__(self, role: str) -> None:
        self.role = role
        self.closed = False

    def execute(self, sql: str):
        assert "current_user" in sql
        return self

    def fetchone(self):
        return (self.role,)

    def close(self) -> None:
        self.closed = True


def fake_driver(connection: FakeConnection, seen: list):
    def driver(conninfo: str, **kwargs):
        seen.append((conninfo, kwargs))
        return connection

    return driver


def test_connect_always_asks_the_driver_for_tls():
    seen: list = []

    connect(f"{URL}?sslmode=prefer", driver=fake_driver(FakeConnection("catalogue_importer"), seen))

    assert "sslmode=require" in seen[0][0]


def test_connect_sets_a_timeout_and_turns_prepared_statements_off():
    seen: list = []

    connect(URL, driver=fake_driver(FakeConnection("catalogue_importer"), seen))

    kwargs = seen[0][1]
    assert kwargs["connect_timeout"] > 0
    assert kwargs["prepare_threshold"] is None  # the pooler does not support them


def test_connect_returns_the_connection_for_the_importer_role():
    connection = FakeConnection("catalogue_importer")

    result = connect(URL, driver=fake_driver(connection, []))

    assert result is connection
    assert not connection.closed


def test_connect_refuses_and_closes_when_logged_in_as_another_role():
    connection = FakeConnection("postgres")  # for example an admin URL pasted by mistake

    with pytest.raises(WrongRoleError):
        connect(URL, driver=fake_driver(connection, []))

    assert connection.closed


def test_driver_errors_become_a_message_without_the_url_or_password():
    def driver(conninfo: str, **kwargs):
        raise psycopg.ProgrammingError(f"bad connection info: {conninfo}")

    with pytest.raises(DatabaseUnavailable) as error:
        connect(URL, driver=driver)

    assert SECRET not in str(error.value)
    assert "ProgrammingError" in str(error.value)


def test_the_real_driver_failing_on_a_bad_url_is_wrapped_without_the_password():
    # libpq rejects the port before any network call, so this is instant (a refused connection
    # would wait out the full connect timeout on Windows).
    bad_port = f"postgresql://catalogue_importer:{SECRET}@db.example.invalid:notaport/postgres"

    with pytest.raises(DatabaseUnavailable) as error:
        connect(bad_port)

    assert SECRET not in str(error.value)


class SslRow:
    def __init__(self, row):
        self.row = row

    def execute(self, sql: str):
        assert "pg_stat_ssl" in sql
        return self

    def fetchone(self):
        return self.row


def test_tls_in_use_is_true_when_postgres_reports_ssl():
    assert tls_in_use(SslRow((True,))) is True


def test_tls_in_use_is_false_when_postgres_reports_no_ssl():
    assert tls_in_use(SslRow((False,))) is False


def test_tls_in_use_is_false_when_there_is_no_row():
    assert tls_in_use(SslRow(None)) is False
