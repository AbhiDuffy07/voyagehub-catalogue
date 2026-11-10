import hashlib
import json

import pytest
import respx

from catalogue.cli import main, read_highlights
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from tests.apifakes import fake_wikidata, fake_wikivoyage, item_claim, string_claim
from tests.dumpfixture import page, write_dump

EMAIL = "pipeline@example.invalid"
DATE = "20261101"
NAME = f"enwikivoyage-{DATE}-pages-articles.xml.bz2"
BASE = f"https://dumps.wikimedia.org/enwikivoyage/{DATE}"
WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"


def quick_client(settings):
    return PoliteClient(settings, sleep=lambda seconds: None)


@pytest.fixture
def dump_bytes(tmp_path):
    path = write_dump(
        tmp_path / "source.xml.bz2",
        [
            page("India", "{{usablecountry}}", page_id=1),
            page("Jaipur", "{{IsPartOf|India}}{{guidecity}}", page_id=5),
        ],
    )
    return path.read_bytes()


@pytest.fixture
def api(dump_bytes):
    sha1 = hashlib.sha1(dump_bytes).hexdigest()  # noqa: S324
    with respx.mock:
        respx.get(f"{BASE}/enwikivoyage-{DATE}-sha1sums.txt").respond(200, text=f"{sha1}  {NAME}\n")
        respx.get(f"{BASE}/{NAME}").respond(200, content=dump_bytes)
        respx.get(WV).mock(side_effect=fake_wikivoyage({"India": "Q668", "Jaipur": "Q1"}))
        respx.get(WD).mock(
            side_effect=fake_wikidata(
                {
                    "Q668": {"P297": [string_claim("IN")]},
                    "Q1": {"P17": [item_claim("Q668")]},
                }
            )
        )
        yield


def discover(argv, capsys, tmp_path):
    args = ["discover", "--dumps-dir", str(tmp_path / "dumps"), "--out-dir", str(tmp_path / "out")]
    if "--overrides-dir" not in argv:  # never read the repo's real overrides in a test
        args += ["--overrides-dir", str(tmp_path / "no-overrides")]
    if "--data-dir" not in argv:  # never touch the repo's committed data/slugs.json in a test
        # Like the real repo, the registry already exists (empty here); a missing one is an error.
        folder = tmp_path / "data"
        folder.mkdir(exist_ok=True)
        (folder / "slugs.json").write_text(
            json.dumps({"schema_version": 1, "entries": {}}), encoding="utf-8"
        )
        args += ["--data-dir", str(folder)]
    code = main(
        [*args, *argv],
        environ={"CONTACT_EMAIL": EMAIL},
        env_file=None,
        client_factory=quick_client,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_discover_downloads_verifies_runs_and_prints_counts(api, capsys, tmp_path):
    code, out, err = discover(["--dump-date", DATE], capsys, tmp_path)

    assert (code, err) == (0, "")
    assert "destinations: 1" in out
    assert "India (IN): 1" in out
    assert (tmp_path / "dumps" / DATE / NAME).is_file()
    rows = (tmp_path / "out" / DATE / "destinations.jsonl").read_text(encoding="utf-8")
    assert json.loads(rows)["slug"] == "jaipur"


def test_discover_writes_the_slug_registry_to_the_data_dir_not_the_out_dir(api, capsys, tmp_path):
    code, _, err = discover(["--dump-date", DATE], capsys, tmp_path)

    assert (code, err) == (0, "")
    registry = json.loads((tmp_path / "data" / "slugs.json").read_text(encoding="utf-8"))
    assert registry["entries"]["5"]["slug"] == "jaipur"
    assert not (tmp_path / "out" / "slugs.json").exists()


def test_discover_keeps_slugs_already_in_the_data_dir_registry(api, capsys, tmp_path):
    folder = tmp_path / "other-data"
    folder.mkdir()
    (folder / "slugs.json").write_text(
        json.dumps(
            {"schema_version": 1, "entries": {"5": {"slug": "jaipur-classic", "title": "Jaipur"}}}
        ),
        encoding="utf-8",
    )

    code, _, err = discover(["--dump-date", DATE, "--data-dir", str(folder)], capsys, tmp_path)

    assert (code, err) == (0, "")
    rows = (tmp_path / "out" / DATE / "destinations.jsonl").read_text(encoding="utf-8")
    assert json.loads(rows)["slug"] == "jaipur-classic"


def test_a_missing_registry_stops_discover_before_any_download_or_output(api, capsys, tmp_path):
    missing = tmp_path / "wrong-place"

    code, _, err = discover(["--dump-date", DATE, "--data-dir", str(missing)], capsys, tmp_path)

    assert code == 1
    assert "slug registry" in err and "--init-registry" in err
    assert "Traceback" not in err
    assert not missing.exists()
    assert not (tmp_path / "dumps").exists()
    assert not (tmp_path / "out").exists()


def test_init_registry_allows_a_genuine_first_run(api, capsys, tmp_path):
    folder = tmp_path / "fresh-data"

    code, _, err = discover(
        ["--dump-date", DATE, "--data-dir", str(folder), "--init-registry"], capsys, tmp_path
    )

    assert (code, err) == (0, "")
    registry = json.loads((folder / "slugs.json").read_text(encoding="utf-8"))
    assert registry["entries"]["5"]["slug"] == "jaipur"


def test_discover_never_prints_the_contact_address(api, capsys, tmp_path):
    _, out, err = discover(["--dump-date", DATE, "--detail", "IN"], capsys, tmp_path)

    assert EMAIL not in out + err


def test_discover_needs_a_dump_date(capsys, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        discover([], capsys, tmp_path)

    assert exit_info.value.code == 2


def test_a_malformed_dump_date_is_a_clean_error(capsys, tmp_path):
    code, out, err = discover(["--dump-date", "latest"], capsys, tmp_path)

    assert code == 1
    assert "dump date" in err
    assert "Traceback" not in err


def test_a_detail_report_for_an_unknown_country_is_a_clean_error(api, capsys, tmp_path):
    code, _, err = discover(["--dump-date", DATE, "--detail", "ZZ"], capsys, tmp_path)

    assert code == 1
    assert "ZZ" in err


def test_highlight_titles_are_read_one_per_line_skipping_blanks_and_comments(tmp_path):
    path = tmp_path / "batch.txt"
    path.write_text("# proving batch\nJaipur\n\n  Agra  \n", encoding="utf-8")

    assert read_highlights(path) == ["Jaipur", "Agra"]


def test_a_missing_highlight_file_is_an_error_that_names_the_problem(tmp_path):
    with pytest.raises(PipelineError, match="highlight file"):
        read_highlights(tmp_path / "nope.txt")


def test_the_discover_help_is_listed(capsys):
    with pytest.raises(SystemExit):
        main(["--help"], environ={}, env_file=None)

    assert "discover" in capsys.readouterr().out


def test_discover_applies_the_country_iso_overrides_file(api, capsys, tmp_path):
    folder = tmp_path / "ov"
    folder.mkdir()
    (folder / "country_iso.csv").write_text(
        "country,country_iso,reason\nIndia,ZZ,test only\n", encoding="utf-8"
    )

    code, out, err = discover(
        ["--dump-date", DATE, "--overrides-dir", str(folder)], capsys, tmp_path
    )

    assert (code, err) == (0, "")
    rows = (tmp_path / "out" / DATE / "destinations.jsonl").read_text(encoding="utf-8")
    assert json.loads(rows)["country_iso"] == "ZZ"


def test_an_override_for_an_unknown_country_is_a_clean_error(api, capsys, tmp_path):
    folder = tmp_path / "ov"
    folder.mkdir()
    (folder / "country_iso.csv").write_text(
        "country,country_iso,reason\nAtlantis,AT,test only\n", encoding="utf-8"
    )

    code, _, err = discover(["--dump-date", DATE, "--overrides-dir", str(folder)], capsys, tmp_path)

    assert code == 1
    assert "Atlantis" in err
