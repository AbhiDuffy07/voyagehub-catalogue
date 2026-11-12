import shutil

import pytest
import respx

from catalogue.cli import main
from catalogue.http import PoliteClient
from tests.apifakes import (
    coordinate_claim,
    entity,
    fake_wikidata_full,
    fake_wikipedia,
    fake_wikivoyage,
    item_claim,
)
from tests.enrichfixture import article, listing, write_inputs

EMAIL = "pipeline@example.invalid"
WV = "https://en.wikivoyage.org/w/api.php"
WP = "https://en.wikipedia.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"


def quick_client(settings):
    return PoliteClient(settings, sleep=lambda seconds: None)


@pytest.fixture
def folders(tmp_path):
    discover, listings = write_inputs(
        tmp_path / "work",
        articles=[
            article(1, "Jaipur", qid="Q10"),
            article(2, "India", type_="country", role="browse", qid="Q668", status=None),
        ],
        listings=[listing(1, 1, "Amber Fort", wikidata="Q100")],
    )
    # Like the real jobs: discover always writes the world folder; fetch-listings adds -CC.
    for name, folder, target in (
        ("discover", discover, "20261101"),
        ("listings", listings, "20261101-IN"),
    ):
        (tmp_path / name).mkdir()
        shutil.move(str(folder), str(tmp_path / name / target))
    return tmp_path


@pytest.fixture
def api():
    entities = {
        "Q10": entity({"P17": [item_claim("Q668")]}),
        "Q668": entity({}),
        "Q100": entity({"P17": [item_claim("Q668")], "P625": [coordinate_claim(26.99, 75.85)]}),
        "Q9259": entity({}, label="World Heritage Site"),
    }
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage({}))
        respx.get(WP).mock(side_effect=fake_wikipedia({}))
        respx.get(WD).mock(side_effect=fake_wikidata_full(entities))
        yield


def enrich(folders, capsys, *extra, environ=None):
    argv = [
        "enrich-wikidata",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--discover-dir",
        str(folders / "discover"),
        "--listings-dir",
        str(folders / "listings"),
        "--out-dir",
        str(folders / "enrich"),
        *extra,
    ]
    code = main(
        argv,
        environ={"CONTACT_EMAIL": EMAIL} if environ is None else environ,
        env_file=None,
        client_factory=quick_client,
    )
    return code, capsys.readouterr()


def test_runs_prints_counts_and_the_output_folder(folders, api, capsys):
    code, out = enrich(folders, capsys)
    assert code == 0
    assert "listing coordinates filled: 1 of 1" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    assert (folders / "enrich" / "20261101-IN" / "listing_facts.jsonl").is_file()
    assert EMAIL not in out.out + out.err


def test_a_detail_report_can_be_requested(folders, api, capsys):
    code, _ = enrich(folders, capsys, "--detail", "IN")
    assert code == 0
    assert (folders / "enrich" / "20261101-IN" / "detail-IN.md").is_file()


def test_missing_contact_email_is_one_clear_error(folders, api, capsys):
    code, out = enrich(folders, capsys, environ={})
    assert code == 1
    assert out.err.startswith("error:") and "Traceback" not in out.err


def test_missing_inputs_are_one_clear_error_without_a_request(folders, capsys):
    shutil.rmtree(folders / "listings" / "20261101-IN")
    with respx.mock:
        route = respx.get(WD).mock(side_effect=fake_wikidata_full({}))
        code, out = enrich(folders, capsys)
        assert not route.called
    assert code == 1 and "manifest" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(folders, capsys):
    argv = ["enrich-wikidata", "--dump-date", "2026-11-01", "--country", "IN"]
    code = main(argv, environ={"CONTACT_EMAIL": EMAIL}, env_file=None, client_factory=quick_client)
    assert code == 1
    assert "YYYYMMDD" in capsys.readouterr().err
