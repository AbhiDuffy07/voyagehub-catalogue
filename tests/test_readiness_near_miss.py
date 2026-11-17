import csv

from catalogue.readiness.near_miss import NO_SLUG, near_miss_csv, near_miss_markdown, near_misses
from tests.readinessfixture import run


def row(country, failed=(), ready=None, action="none"):
    return {
        "country_iso": country,
        "failed_rules": list(failed),
        "ready": not failed if ready is None else ready,
        "publish_action": action,
    }


ROWS = [
    row("IN", ["photographed_places"]),
    row("IN", ["photographed_places"]),
    row("IN", ["hero_photo"]),
    row("IN", ["hero_photo", "photographed_places"]),  # two rules: not a near miss
    row("US", ["checked_block"]),
    row("US", [], action="blocked_no_slug"),
    row("US", [], action="publish"),
    row(None, ["summary"]),
]


def test_only_cities_failing_exactly_one_rule_are_counted_per_rule_and_country():
    table = near_misses(ROWS)

    assert table["IN"] == {"photographed_places": 2, "hero_photo": 1}
    assert table["US"] == {"checked_block": 1, NO_SLUG: 1}
    assert table["unknown"] == {"summary": 1}


def test_a_ready_city_counts_as_a_near_miss_only_when_it_waits_for_a_slug():
    table = near_misses([row("IN", [], action="publish"), row("IN", [], action="keep")])

    assert table == {}


def test_the_csv_is_sorted_and_has_one_line_per_country_and_rule_with_a_world_total():
    lines = list(csv.DictReader(near_miss_csv(near_misses(ROWS)).splitlines()))

    assert lines[0] == {"country_iso": "ALL", "reason": "checked_block", "cities": "1"}
    assert {"country_iso": "IN", "reason": "photographed_places", "cities": "2"} in lines
    totals = {r["reason"]: int(r["cities"]) for r in lines if r["country_iso"] == "ALL"}
    assert totals == {
        "checked_block": 1,
        "hero_photo": 1,
        NO_SLUG: 1,
        "photographed_places": 2,
        "summary": 1,
    }


def test_the_markdown_has_an_overall_table_and_the_biggest_countries():
    text = near_miss_markdown(near_misses(ROWS), top=1)

    assert "| photographed_places | 2 |" in text
    assert "| IN | 3 |" in text
    assert "| US |" not in text  # only the top country is listed


def test_a_run_writes_the_near_miss_files_and_a_report_section(tmp_path):
    result, _ = run(tmp_path)

    assert (result.output_dir / "near_miss.csv").is_file()
    assert "Near misses" in (result.output_dir / "near_miss.md").read_text(encoding="utf-8")
    assert "Near misses" in (result.output_dir / "report.md").read_text(encoding="utf-8")
