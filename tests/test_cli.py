import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

from catalogue import __version__
from catalogue.cli import main

EMAIL = "pipeline@example.invalid"
GOOD_ENV = {"CONTACT_EMAIL": EMAIL}
SECRET = "s3cret-pw"
DB_ENV = {**GOOD_ENV, "CATALOGUE_DATABASE_URL": f"postgresql://u:{SECRET}@db.example.invalid/db"}
API = "https://en.wikivoyage.org/w/api.php"


class FakeConnection:
    def __init__(self, role: str = "catalogue_importer", ssl: bool = True) -> None:
        self.role = role
        self.ssl = ssl
        self._row: tuple = ()

    def execute(self, sql: str):
        self._row = (self.role,) if "current_user" in sql else (self.ssl,)
        return self

    def fetchone(self):
        return self._row

    def close(self) -> None:
        pass


def run(argv, environ, capsys, **kwargs):
    code = main(argv, environ=environ, env_file=None, **kwargs)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version_prints_the_version(capsys):
    code, out, _ = run(["version"], {}, capsys)

    assert code == 0
    assert out.strip() == __version__


def test_help_lists_the_check_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"], environ={}, env_file=None)

    assert exit_info.value.code == 0
    assert "check" in capsys.readouterr().out


def test_a_command_is_required():
    with pytest.raises(SystemExit) as exit_info:
        main([], environ={}, env_file=None)

    assert exit_info.value.code == 2


def test_check_reports_good_settings_without_printing_the_email(capsys):
    code, out, _ = run(["check"], GOOD_ENV, capsys)

    assert code == 0
    assert "settings: ok" in out
    assert EMAIL not in out


def test_check_names_a_missing_setting_and_exits_1(capsys):
    code, _, err = run(["check"], {}, capsys)

    assert code == 1
    assert "CONTACT_EMAIL" in err


def test_check_db_needs_the_database_url(capsys):
    code, _, err = run(["check", "--db"], GOOD_ENV, capsys)

    assert code == 1
    assert "CATALOGUE_DATABASE_URL" in err


def test_check_db_confirms_role_and_tls_without_printing_the_url(capsys):
    driver = lambda conninfo, **kwargs: FakeConnection()  # noqa: E731

    code, out, err = run(["check", "--db"], DB_ENV, capsys, db_driver=driver)

    assert code == 0
    assert "database: ok" in out
    assert SECRET not in out + err


def test_check_db_fails_when_the_connection_is_not_encrypted(capsys):
    driver = lambda conninfo, **kwargs: FakeConnection(ssl=False)  # noqa: E731

    code, _, err = run(["check", "--db"], DB_ENV, capsys, db_driver=driver)

    assert code == 1
    assert "TLS" in err


def test_check_db_fails_for_the_wrong_role_with_a_clear_message(capsys):
    driver = lambda conninfo, **kwargs: FakeConnection(role="postgres")  # noqa: E731

    code, _, err = run(["check", "--db"], DB_ENV, capsys, db_driver=driver)

    assert code == 1
    assert "catalogue_importer" in err
    assert SECRET not in err


@respx.mock
def test_check_network_makes_one_polite_call_to_wikivoyage(capsys):
    route = respx.get(API).respond(200, json={"query": {"general": {"sitename": "Wikivoyage"}}})

    code, out, _ = run(["check", "--network"], GOOD_ENV, capsys)

    assert code == 0
    assert "Wikivoyage" in out
    assert route.call_count == 1
    assert route.calls.last.request.headers["user-agent"].endswith("bot")
    assert EMAIL not in out


@respx.mock
def test_check_network_reports_a_failure_and_exits_1(capsys):
    respx.get(API).respond(404)

    code, _, err = run(["check", "--network"], GOOD_ENV, capsys)

    assert code == 1
    assert "404" in err


@respx.mock
def test_check_network_reports_an_unexpected_reply_shape(capsys):
    respx.get(API).mock(return_value=httpx.Response(200, json={"unexpected": True}))

    code, _, err = run(["check", "--network"], GOOD_ENV, capsys)

    assert code == 1
    assert "unexpected" in err.lower()


def test_python_dash_m_catalogue_runs_the_cli():
    root = Path(__file__).resolve().parent.parent

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "catalogue", "version"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == __version__
