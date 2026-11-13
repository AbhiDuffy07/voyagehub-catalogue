import json
import shutil

import pytest
import respx

from catalogue.cli import main
from catalogue.http import PoliteClient
from tests.apifakes import fake_wikidata, string_claim
from tests.enrichfixture import destination_row, write_enrich_output
from tests.photofakes import COMMONS, commons_file, fake_commons
from tests.test_photos_run import _sha

EMAIL = "pipeline@example.invalid"
WD = "https://www.wikidata.org/w/api.php"


def quick_client(settings):
    return PoliteClient(settings, sleep=lambda seconds: None)


@pytest.fixture
def dirs(tmp_path):
    built = write_enrich_output(
        tmp_path / "built",
        destinations=[{**destination_row(1, "Jaipur", qid="Q1"), "image": "Fort.jpg"}],
        listings=[],
    )
    enrich = tmp_path / "enrich"
    enrich.mkdir()
    shutil.move(str(built), str(enrich / "20261101-IN"))
    listings = tmp_path / "listings" / "20261101-IN"
    listings.mkdir(parents=True)
    (listings / "listings.jsonl").write_text(
        json.dumps({"kind": "see", "country_iso": "IN"}) + "\n", encoding="utf-8"
    )
    (listings / "manifest.json").write_text(
        json.dumps({"outputs_sha256": {"listings.jsonl": _sha(listings / "listings.jsonl")}}),
        encoding="utf-8",
    )
    manifest_path = enrich / "20261101-IN" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["listings_manifest_sha256"] = _sha(listings / "manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


@pytest.fixture
def api():
    with respx.mock:
        respx.get(WD).mock(side_effect=fake_wikidata({"Q1": {"P18": [string_claim("Fort.jpg")]}}))
        respx.get(COMMONS).mock(side_effect=fake_commons({"File:Fort.jpg": commons_file()}))
        yield


def photos(dirs, capsys, *extra, environ=None):
    argv = [
        "photo-candidates",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--enrich-dir",
        str(dirs / "enrich"),
        "--listings-dir",
        str(dirs / "listings"),
        "--out-dir",
        str(dirs / "photos"),
        *extra,
    ]
    code = main(
        argv,
        environ={"CONTACT_EMAIL": EMAIL} if environ is None else environ,
        env_file=None,
        client_factory=quick_client,
    )
    return code, capsys.readouterr()


def test_runs_prints_counts_and_the_output_folder(dirs, api, capsys):
    code, out = photos(dirs, capsys)
    assert code == 0
    assert "candidate rows: 1" in out.out and "usable rows: 1" in out.out
    assert "coverage articles: 1 of 1 (100.0%)" in out.out
    assert "coverage listings: 0 of 1 (0.0%)" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "photos" / "20261101-IN" / "photo_candidates.jsonl").is_file()
    assert EMAIL not in out.out + out.err and "Fort.jpg" not in out.out


def test_missing_contact_email_is_one_clear_error(dirs, api, capsys):
    code, out = photos(dirs, capsys, environ={})
    assert code == 1 and out.err.startswith("error:") and "Traceback" not in out.err


def test_missing_enrich_output_is_one_clear_error_without_a_request(dirs, capsys):
    shutil.rmtree(dirs / "enrich" / "20261101-IN")
    with respx.mock:
        route = respx.get(WD).mock(side_effect=fake_wikidata({}))
        code, out = photos(dirs, capsys)
        assert not route.called
    assert code == 1 and "manifest" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    argv = ["photo-candidates", "--dump-date", "2026-11-01"]
    code = main(argv, environ={"CONTACT_EMAIL": EMAIL}, env_file=None, client_factory=quick_client)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err


def test_progress_lines_show_counts_and_no_names(dirs, api, capsys):
    code, out = photos(dirs, capsys)
    assert code == 0 and out.err == ""
    progress = [line for line in out.out.splitlines() if line.startswith("progress:")]
    assert "progress: Wikidata media items 1 of 1 (100%) done" in progress
    assert "progress: Commons files 1 of 1 (100%) done" in progress
    assert not any("Fort" in line or "Jaipur" in line for line in progress)
