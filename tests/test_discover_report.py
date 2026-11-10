import csv
from datetime import date

import pytest
import respx

from catalogue.discover.report import write_research
from catalogue.discover.run import run_discover
from catalogue.errors import PipelineError
from tests.apifakes import fake_wikidata, fake_wikivoyage
from tests.dumpfixture import page, write_dump
from tests.synthetic_world import ENTITIES, IDS, PAGES

WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"

# A country with no destinations at all must still be listed.
EXTRA = [page("Bhutan", "{{IsPartOf|Asia}}{{usablecountry}}", page_id=20)]


@pytest.fixture
def dump_dir(client, tmp_path):
    dump = write_dump(tmp_path / "d.xml.bz2", [*PAGES, *EXTRA])
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(ENTITIES))
        result = run_discover(
            client,
            dump_path=dump,
            dump_date="20261101",
            out_dir=tmp_path / "out",
            today=date(2026, 11, 10),
        )
    return result.output_dir


def rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def research(dump_dir, **kwargs):
    kwargs.setdefault("detail_countries", [])
    return write_research(dump_dir, **kwargs)


def test_all_articles_counts_every_type_by_status(dump_dir):
    folder = research(dump_dir)

    table = {r["type"]: r for r in rows(folder / "all_articles.csv")}
    city = table["city"]
    assert [int(city[k]) for k in ("outline", "usable", "guide", "star", "no_status", "total")] == [
        1,
        4,
        1,
        0,
        0,
        6,
    ]
    assert int(table["continent"]["usable"]) == 2
    assert int(table["itinerary"]["total"]) == 1
    assert int(table["(no type)"]["no_status"]) == 2
    assert int(table["all types"]["total"]) == 16  # every non-redirect page, Bhutan included


def test_the_folder_is_research_under_the_dump_folder(dump_dir):
    assert research(dump_dir) == dump_dir / "research"


def test_countries_are_sorted_by_destinations_and_include_empty_ones(dump_dir):
    table = rows(research(dump_dir) / "countries.csv")

    assert [r["country"] for r in table][:2] == ["India", "Nepal"]
    names = [r["country"] for r in table]
    assert "Bhutan" in names
    assert names[-1] == "(unassigned)"
    india = table[0]
    assert (india["country_iso"], india["continent"]) == ("IN", "Asia")
    assert [int(india[k]) for k in ("city_usable", "city_guide", "city_star")] == [1, 1, 0]
    assert [int(india[k]) for k in ("park_usable", "outline", "total")] == [1, 1, 3]
    bhutan = next(r for r in table if r["country"] == "Bhutan")
    assert (bhutan["continent"], int(bhutan["total"])) == ("Asia", 0)


def test_a_country_destinations_file_lists_every_status_with_links(dump_dir):
    folder = research(dump_dir, detail_countries=["IN"])

    table = rows(folder / "india_destinations.csv")
    by_name = {r["name"]: r for r in table}
    assert set(by_name) == {"Jaipur", "Agra", "Ranthambore", "Outlineville"}
    assert by_name["Outlineville"]["status"] == "outline"
    assert by_name["Jaipur"]["region"] == "Rajasthan"
    assert by_name["Jaipur"]["type"] == "city"
    assert by_name["Jaipur"]["link"] == "https://en.wikivoyage.org/wiki/Jaipur"
    assert by_name["Agra"]["region"] == "(directly under the country)"


def test_an_unknown_detail_country_is_an_error(dump_dir):
    with pytest.raises(PipelineError, match="ZZ"):
        research(dump_dir, detail_countries=["ZZ"])


def test_the_summary_defines_the_terms_and_gives_the_headline_numbers(dump_dir):
    text = (research(dump_dir) / "summary.md").read_text(encoding="utf-8")

    assert "Articles in the dump: 16" in text
    assert "Candidate destinations: 6" in text
    assert "outline" in text.lower()
    assert "usable" in text.lower()
    assert "conflict" in text.lower()


def test_the_summary_compares_with_wikivoyages_category_totals(dump_dir):
    folder = research(dump_dir, category_totals={"usable": 10, "guide": 1, "star": 0})

    text = (folder / "summary.md").read_text(encoding="utf-8")
    assert "| usable | 12 | 10 | +2 |" in text
    assert "| guide | 1 | 1 | 0 |" in text


def test_without_discover_output_the_error_says_what_to_run(tmp_path):
    with pytest.raises(PipelineError, match="run discover first"):
        write_research(tmp_path, detail_countries=[])


def test_running_the_report_twice_gives_identical_files(dump_dir):
    first = research(dump_dir, detail_countries=["IN"])
    before = {p.name: p.read_bytes() for p in first.iterdir()}

    second = research(dump_dir, detail_countries=["IN"])

    assert {p.name: p.read_bytes() for p in second.iterdir()} == before
