import csv
import hashlib
import json
from datetime import date

import pytest
import respx

from catalogue.discover.run import run_discover
from tests.apifakes import fake_wikidata, fake_wikivoyage, item_claim, string_claim
from tests.dumpfixture import page, write_dump

WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"
TODAY = date(2026, 11, 10)

PAGES = [
    page("Asia", "{{usablecontinent}}", page_id=2),
    page("India", "{{IsPartOf|Asia}}{{usablecountry}}", page_id=1),
    page("Nepal", "{{IsPartOf|Asia}}{{usablecountry}}", page_id=3),
    page("Rajasthan", "{{IsPartOf|India}}{{usableregion}}", page_id=4),
    page("Jaipur", "{{IsPartOf|Rajasthan}}{{guidecity}}", page_id=5, revision=905),
    page("Agra", "{{IsPartOf|India}}{{usablecity}}", page_id=6),
    page("Pokhara", "{{IsPartOf|Nepal}}{{usablecity}}", page_id=7),
    page("Outlineville", "{{IsPartOf|India}}{{outlinecity}}", page_id=8),
    page("Europe", "{{usablecontinent}}", page_id=10),
    page("Gibraltar", "{{IsPartOf|Europe}}{{usablecity}}", page_id=9),
    page("Ranthambore", "{{IsPartOf|Rajasthan}}{{usablepark}}", page_id=11),
    page("Talsi", "{{IsPartOf|India}}{{usablecity}}{{outlinecity}}", page_id=12),
    page("Kustavi", "{{IsPartOf|Nepal|Elsewhere}}{{usablecity}}", page_id=13),
    page("Golden Triangle", "{{IsPartOf|India}}{{usableitinerary}}", page_id=14),
    page("Notes page", "just text, no templates", page_id=15),
    page("Old Jaipur", "", page_id=16, redirect="Jaipur"),
]
IDS = {
    "India": "Q668",
    "Nepal": "Q837",
    "Jaipur": "Q1",
    "Agra": "Q2",
    "Pokhara": "Q3",
    "Ranthambore": "Q4",
    "Gibraltar": "Q5",
}
ENTITIES = {
    "Q668": {"P297": [string_claim("IN")]},
    "Q837": {"P297": [string_claim("NP")]},
    "Q1": {"P17": [item_claim("Q668")]},
    "Q2": {"P17": [item_claim("Q668")]},
    "Q3": {"P17": [item_claim("Q668")]},  # Wikidata says India, the tree says Nepal
    "Q4": {},
    "Q5": {"P17": [item_claim("Q145")]},
    "Q145": {"P297": [string_claim("GB")]},
}


@pytest.fixture
def api():
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(ENTITIES))
        yield


@pytest.fixture
def dump(tmp_path):
    return write_dump(tmp_path / "dump.xml.bz2", PAGES)


def run(client, dump, tmp_path, **kwargs):
    return run_discover(
        client,
        dump_path=dump,
        dump_date="20261101",
        out_dir=tmp_path / "out",
        today=TODAY,
        **kwargs,
    )


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def records(result):
    lines = (result.output_dir / "destinations.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["title"]: r for r in map(json.loads, lines)}


def test_destinations_are_candidates_only_with_full_provenance(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    rows = records(result)
    assert set(rows) == {"Jaipur", "Agra", "Pokhara", "Ranthambore", "Gibraltar", "Kustavi"}
    jaipur = rows["Jaipur"]
    assert jaipur["wikivoyage_page_id"] == 5
    assert jaipur["slug"] == "jaipur"
    assert (jaipur["type"], jaipur["status"]) == ("city", "guide")
    assert (jaipur["country"], jaipur["country_iso"]) == ("India", "IN")
    assert jaipur["ancestors"] == ["Rajasthan", "India"]
    assert jaipur["wikidata_id"] == "Q1"
    assert jaipur["confirmation"] == "confirmed"
    assert jaipur["source_url"].endswith("title=Jaipur&oldid=905")
    assert jaipur["licence"] == "CC BY-SA 4.0"
    assert jaipur["attribution"] == "Wikivoyage contributors"
    assert jaipur["read_at"] == "2026-11-10"
    assert jaipur["dump_date"] == "2026-11-01"


def test_a_conflict_gets_no_country_and_is_listed(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    pokhara = records(result)["Pokhara"]
    assert (pokhara["confirmation"], pokhara["country_iso"]) == ("conflict", None)
    conflicts = read_csv(result.output_dir / "conflicts.csv")
    assert [(c["title"], c["tree_country"], c["wikidata_country_iso"]) for c in conflicts] == [
        ("Pokhara", "Nepal", "IN")
    ]


def test_pages_the_tree_cannot_place_are_listed_with_a_reason(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    unassigned = {r["title"]: r for r in read_csv(result.output_dir / "unassigned.csv")}
    assert unassigned["Gibraltar"]["reason"] == "no_country_above"
    assert unassigned["Gibraltar"]["wikidata_country_iso"] == "GB"
    assert unassigned["Kustavi"]["reason"] == "unparsed_parent"


def test_pages_with_an_ambiguous_status_are_excluded_and_listed(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    assert "Talsi" not in records(result)
    excluded = read_csv(result.output_dir / "excluded.csv")
    assert [(e["title"], e["reason"]) for e in excluded] == [("Talsi", "ambiguous_status")]


def test_counts_per_country(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    rows = {r["country"]: r for r in read_csv(result.output_dir / "counts_by_country.csv")}
    india = rows["India"]
    assert india["country_iso"] == "IN"
    assert [int(india[k]) for k in ("city", "district", "park")] == [2, 0, 1]
    assert [int(india[k]) for k in ("usable", "guide", "star", "total")] == [2, 1, 0, 3]
    assert int(india["outline"]) == 1
    assert [int(india[k]) for k in ("confirmed", "unconfirmed", "conflict")] == [2, 1, 0]
    assert int(rows["Nepal"]["conflict"]) == 1
    assert int(rows["(unassigned)"]["total"]) == 2


def test_the_manifest_records_the_dump_and_the_totals(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dump_date"] == "20261101"
    assert manifest["dump_sha1"] == hashlib.sha1(dump.read_bytes()).hexdigest()  # noqa: S324
    assert manifest["country_filter"] is None
    assert manifest["totals"]["destinations"] == 6
    assert manifest["totals"]["confirmation"] == {
        "confirmed": 2,
        "unconfirmed": 1,
        "conflict": 1,
        "unassigned": 2,
    }


def test_a_country_filter_keeps_that_country_and_writes_to_its_own_folder(
    client, api, dump, tmp_path
):
    result = run(client, dump, tmp_path, country_iso="IN")

    assert result.output_dir.name == "20261101-IN"
    assert set(records(result)) == {"Jaipur", "Agra", "Ranthambore"}


def test_a_rerun_gives_identical_files_and_the_same_slugs(client, api, dump, tmp_path):
    first = run(client, dump, tmp_path)
    before = (first.output_dir / "destinations.jsonl").read_bytes()

    second = run(client, dump, tmp_path)

    assert (second.output_dir / "destinations.jsonl").read_bytes() == before


def test_a_detail_report_shows_regions_and_the_highlighted_destinations(
    client, api, dump, tmp_path
):
    result = run(
        client,
        dump,
        tmp_path,
        detail_countries=["IN"],
        highlight_titles=["Jaipur", "Agra", "Kochi"],
    )

    report = (result.output_dir / "detail-IN.md").read_text(encoding="utf-8")
    assert "Rajasthan" in report
    assert "| Jaipur | guide city | Rajasthan" in report
    assert "| Kochi | not in the dump" in report
    assert "Pokhara" not in report


def test_the_summary_lists_world_totals_and_top_countries(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    text = result.summary(top=10)

    assert "destinations: 6" in text
    assert "India" in text


def articles(result):
    lines = (result.output_dir / "articles.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["title"]: r for r in map(json.loads, lines)}


def test_every_article_is_kept_not_only_the_candidates(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    kept = articles(result)
    assert len(kept) == 15  # every non-redirect page in the fixture
    assert "Asia" in kept and "Rajasthan" in kept and "Outlineville" in kept
    assert "Old Jaipur" not in kept  # a redirect is an alias, listed in redirects.csv


def test_each_article_has_a_role_so_nothing_is_dropped_silently(client, api, dump, tmp_path):
    kept = articles(run(client, dump, tmp_path))

    roles = {title: row["role"] for title, row in kept.items()}
    assert roles["Jaipur"] == "destination"
    assert roles["Outlineville"] == "destination_outline"
    assert roles["Asia"] == "browse"
    assert roles["India"] == "browse"
    assert roles["Rajasthan"] == "browse"
    assert roles["Golden Triangle"] == "later_wave"
    assert kept["Golden Triangle"]["wave_rank"] == 1
    assert roles["Notes page"] == "no_type"


def test_articles_carry_their_tree_country_and_continent(client, api, dump, tmp_path):
    kept = articles(run(client, dump, tmp_path))

    jaipur = kept["Jaipur"]
    assert (jaipur["tree_country"], jaipur["tree_country_iso"]) == ("India", "IN")
    assert jaipur["continent"] == "Asia"
    assert jaipur["wikidata_id"] == "Q1"
    assert kept["India"]["tree_country_iso"] == "IN"
    assert kept["India"]["wikidata_id"] == "Q668"
    assert kept["Asia"]["continent"] is None
    assert kept["Gibraltar"]["assignment_problem"] == "no_country_above"
    assert kept["Jaipur"]["licence"] == "CC BY-SA 4.0"


def test_redirects_are_listed(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    assert read_csv(result.output_dir / "redirects.csv") == [
        {"title": "Old Jaipur", "target": "Jaipur"}
    ]


def test_an_iso_override_reaches_the_counts(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path, country_iso_overrides={"Nepal": "NX"})

    rows = {r["country"]: r for r in read_csv(result.output_dir / "counts_by_country.csv")}
    assert rows["Nepal"]["country_iso"] == "NX"
    assert result.totals["overrides_applied"] == ["Nepal"]


def test_the_manifest_counts_articles_by_role(client, api, dump, tmp_path):
    result = run(client, dump, tmp_path)

    assert result.totals["articles_by_role"]["later_wave"] == 1
    assert result.totals["articles_by_role"]["browse"] == 5
