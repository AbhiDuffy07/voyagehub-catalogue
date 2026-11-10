from datetime import date

import pytest
import respx

from catalogue.cli import main, parse_category_totals
from catalogue.discover.run import run_discover
from catalogue.errors import PipelineError
from tests.apifakes import fake_wikidata, fake_wikivoyage
from tests.dumpfixture import write_dump
from tests.synthetic_world import ENTITIES, IDS, PAGES

WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"
DATE = "20261101"


@pytest.fixture
def out_dir(client, tmp_path):
    dump = write_dump(tmp_path / "d.xml.bz2", PAGES)
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(ENTITIES))
        run_discover(
            client,
            dump_path=dump,
            dump_date=DATE,
            out_dir=tmp_path / "out",
            today=date(2026, 11, 10),
        )
    return tmp_path / "out"


def report(argv, out_dir, capsys):
    # No settings at all: the report must work offline and without a contact address.
    code = main(["report", "--out-dir", str(out_dir), *argv], environ={}, env_file=None)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_report_writes_the_research_files_and_says_where(out_dir, capsys):
    code, out, err = report(["--dump-date", DATE, "--detail", "IN"], out_dir, capsys)

    assert (code, err) == (0, "")
    folder = out_dir / DATE / "research"
    assert str(folder) in out
    names = {p.name for p in folder.iterdir()}
    assert names == {"all_articles.csv", "countries.csv", "india_destinations.csv", "summary.md"}


def test_report_passes_the_category_totals_to_the_summary(out_dir, capsys):
    report(["--dump-date", DATE, "--category-total", "usable=10"], out_dir, capsys)

    text = (out_dir / DATE / "research" / "summary.md").read_text(encoding="utf-8")
    assert "| usable | 11 | 10 | +1 |" in text


def test_a_bad_category_total_is_a_clean_error(out_dir, capsys):
    code, _, err = report(["--dump-date", DATE, "--category-total", "usable=lots"], out_dir, capsys)

    assert code == 1
    assert "category total" in err


def test_report_without_discover_output_says_what_to_run(tmp_path, capsys):
    code, _, err = report(["--dump-date", DATE], tmp_path, capsys)

    assert code == 1
    assert "run discover first" in err


@pytest.mark.parametrize("bad", ["usable", "usable=", "other=5", "usable=-1"])
def test_category_totals_must_be_status_equals_a_whole_number(bad):
    with pytest.raises(PipelineError, match="category total"):
        parse_category_totals([bad])


def test_category_totals_are_parsed_into_a_mapping():
    assert parse_category_totals(["usable=11233", "guide=1191"]) == {"usable": 11233, "guide": 1191}
