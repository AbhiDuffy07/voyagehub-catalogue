import shutil

import pytest
import respx

from catalogue.cli import main
from catalogue.http import PoliteClient
from tests.apifakes import fake_wikipedia_summaries
from tests.enrichfixture import destination_row, listing_fact, write_enrich_output

EMAIL = "pipeline@example.invalid"
WP = "https://en.wikipedia.org/w/api.php"
TEXT = "Jaipur is the capital of Rajasthan, a city of palaces in northern India."


def quick_client(settings):
    return PoliteClient(settings, sleep=lambda seconds: None)


@pytest.fixture
def enrich_dir(tmp_path):
    folder = write_enrich_output(
        tmp_path / "built",
        destinations=[destination_row(1, "Jaipur", enwiki="Jaipur")],
        listings=[listing_fact(1, 1, "see:amber-fort", enwiki="Amer Fort")],
    )
    target = tmp_path / "enrich"
    target.mkdir()
    shutil.move(str(folder), str(target / "20261101-IN"))
    return target


@pytest.fixture
def api():
    pages = {
        "Jaipur": {"extract": TEXT, "revid": 1, "timestamp": "2026-11-02T10:00:00Z"},
        "Amer Fort": {"extract": TEXT, "revid": 2, "timestamp": "2026-11-02T10:00:00Z"},
    }
    with respx.mock:
        respx.get(WP).mock(side_effect=fake_wikipedia_summaries(pages))
        yield


def summarise(enrich_dir, capsys, *extra, environ=None):
    argv = [
        "wikipedia-summary",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--enrich-dir",
        str(enrich_dir),
        "--out-dir",
        str(enrich_dir.parent / "summaries"),
        *extra,
    ]
    code = main(
        argv,
        environ={"CONTACT_EMAIL": EMAIL} if environ is None else environ,
        env_file=None,
        client_factory=quick_client,
    )
    return code, capsys.readouterr()


def test_runs_prints_counts_and_the_output_folder(enrich_dir, api, capsys):
    code, out = summarise(enrich_dir, capsys)
    assert code == 0
    assert "summary rows: 2" in out.out and "with text: 2" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    folder = enrich_dir.parent / "summaries" / "20261101-IN"
    assert (folder / "summaries.jsonl").is_file()
    assert EMAIL not in out.out + out.err and "palaces" not in out.out


def test_missing_contact_email_is_one_clear_error(enrich_dir, api, capsys):
    code, out = summarise(enrich_dir, capsys, environ={})
    assert code == 1 and out.err.startswith("error:") and "Traceback" not in out.err


def test_missing_enrich_output_is_one_clear_error_without_a_request(enrich_dir, capsys):
    shutil.rmtree(enrich_dir / "20261101-IN")
    with respx.mock:
        route = respx.get(WP).mock(side_effect=fake_wikipedia_summaries({}))
        code, out = summarise(enrich_dir, capsys)
        assert not route.called
    assert code == 1 and "manifest" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    argv = ["wikipedia-summary", "--dump-date", "2026-11-01"]
    code = main(argv, environ={"CONTACT_EMAIL": EMAIL}, env_file=None, client_factory=quick_client)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
