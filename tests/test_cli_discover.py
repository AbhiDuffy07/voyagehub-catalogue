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
