import json
from datetime import date

import pytest

from catalogue.blocks.inputs import read_stops
from catalogue.blocks.rules import load_block_rules
from catalogue.blocks.run import run_draft_blocks
from catalogue.errors import PipelineError
from tests.blocksfixture import (
    article_row,
    dest_fact,
    fact_row,
    hours,
    listing_row,
    sha,
    write_inputs,
)

TODAY = date(2026, 11, 14)
RULES = load_block_rules()
PARK = (7, "kaziranga", "Kaziranga", "park")


def world(tmp_path, rows, facts=None, photos=(), **kw):
    """rows: listing rows; facts default to one 'own coordinates' row per row."""
    facts = [fact_row(r) for r in rows] if facts is None else facts
    return write_inputs(tmp_path, rows, facts, photos, **kw)


def run(folders, tmp_path, **kw):
    lis, enr, pho = folders
    return run_draft_blocks(
        listings_folder=lis,
        enrich_folder=enr,
        photos_folder=pho,
        discover_folder=lis.parent / "discover",
        summaries_folder=lis.parent / "summaries",
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        **kw,
    )


def city_rows(count=6, start=1, **kw):
    return [listing_row(start + i, f"Place {i}", km_north=i * 0.2, **kw) for i in range(count)]


def test_read_stops_uses_facts_coordinates_kind_and_flags(tmp_path):
    rows = [
        listing_row(1, "Filled", own_coords=False),
        listing_row(2, "Own"),
        listing_row(3, "Disputed"),
        listing_row(4, "Invalid", quality_flags=["coord_invalid"]),
        listing_row(5, "No point", own_coords=False),
        listing_row(6, "Shop", kind="buy"),
        listing_row(7, "Park stop", owner=PARK),
        listing_row(8, "Copy", duplicate_of="see:own"),
        listing_row(9, ""),
    ]
    facts = [
        fact_row(rows[0], coord=(26.95, 75.85), kind="museum"),
        fact_row(rows[1], kind="religious", unesco=True),
        fact_row(rows[2], flags=["wikidata_coord_disagrees"]),
        fact_row(rows[3], coord=None),
        fact_row(rows[4], coord=None),
        fact_row(rows[5]),
        fact_row(rows[6]),
        fact_row(rows[7]),
        fact_row(rows[8]),
    ]
    lis, enr, pho = world(tmp_path, rows, facts, photos=[rows[1]])
    stops, excluded = read_stops(lis, enr, pho, "IN", RULES)
    by_name = {s.name: s for s in stops}
    assert sorted(by_name) == ["Filled", "Own"]
    assert (by_name["Filled"].lat, by_name["Filled"].lon) == (26.95, 75.85)
    assert by_name["Filled"].kind == "museum"
    assert by_name["Own"].unesco and by_name["Own"].has_photo and not by_name["Filled"].has_photo
    assert excluded == {
        "coord_disputed": 1,
        "no_coordinates": 2,
        "section_not_planned": 1,
        "owner_type_not_planned": 1,
        "duplicate": 1,
        "no_name": 1,
    }


def test_listings_sharing_one_copied_point_are_left_out(tmp_path):
    rows = [listing_row(i, f"Copy {i}") for i in range(1, 4)]
    rows += [listing_row(10, "Pair A", km_north=3), listing_row(11, "Pair B", km_north=3)]
    rows += [listing_row(12, "Elsewhere", km_north=6)]
    lis, enr, pho = world(tmp_path, rows)
    stops, excluded = read_stops(lis, enr, pho, "IN", RULES)
    assert sorted(s.name for s in stops) == ["Elsewhere", "Pair A", "Pair B"]
    assert excluded == {"coord_shared": 3}


def test_a_listing_without_a_facts_row_uses_its_own_valid_point(tmp_path):
    rows = [listing_row(1, "Alone"), listing_row(2, "Broken", quality_flags=["coord_invalid"])]
    lis, enr, pho = world(tmp_path, rows, facts=[])
    stops, excluded = read_stops(lis, enr, pho, "IN", RULES)
    assert [s.name for s in stops] == ["Alone"] and excluded == {"no_coordinates": 1}
    assert stops[0].kind is None and not stops[0].unesco


def test_parsed_hours_with_no_schedule_count_as_unknown(tmp_path):
    rows = [listing_row(1, "A", hours_parsed={"always_open": False, "schedule": []})]
    lis, enr, pho = world(tmp_path, rows)
    stops, _ = read_stops(lis, enr, pho, "IN", RULES)
    assert stops[0].hours is None


def test_end_to_end_files_totals_and_determinism(tmp_path):
    rows = [*city_rows(6, kind="see"), *city_rows(5, start=20, owner=(2, "agra", "Agra", "city"))]
    folders = world(tmp_path, rows)
    first = run(folders, tmp_path)
    folder = first.output_dir
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rules_version"] == 2 and manifest["country_filter"] == "IN"
    names = {
        "areas.jsonl",
        "area_travel.jsonl",
        "city_summary.csv",
        "day_blocks.jsonl",
        "day_block_stops.jsonl",
        "unblocked_stops.csv",
        "report.md",
        "spot_check_sample.md",
    }
    assert set(manifest["outputs_sha256"]) == names
    for name, digest in manifest["outputs_sha256"].items():
        assert sha(folder / name) == digest
    blocks = [json.loads(x) for x in (folder / "day_blocks.jsonl").read_text("utf-8").splitlines()]
    assert blocks and all(b["status"] == "draft" and b["method"] == "estimate" for b in blocks)
    assert all(b["rules_version"] == 2 and b["owner_slug"] in {"jaipur", "agra"} for b in blocks)
    before = {n: (folder / n).read_bytes() for n in names}
    second = run(folders, tmp_path)
    assert {n: (second.output_dir / n).read_bytes() for n in names} == before


def test_report_has_blocks_and_days_per_city_and_exclusions(tmp_path):
    rows = [*city_rows(8, kind="see"), listing_row(99, "Shop", kind="buy")]
    report = (run(world(tmp_path, rows), tmp_path).output_dir / "report.md").read_text("utf-8")
    assert "Jaipur" in report and "days" in report.lower()
    assert "section_not_planned" in report and "estimate" in report


def test_block_rows_carry_stop_facts(tmp_path):
    rows = city_rows(3, hours_parsed=hours(("08:00", "18:00")))
    out = run(world(tmp_path, rows), tmp_path).output_dir
    stops = [json.loads(x) for x in (out / "day_block_stops.jsonl").read_text("utf-8").splitlines()]
    assert len(stops) == 3 and [s["position"] for s in stops] == [1, 2, 3]
    assert all(s["duration_basis"] == "default" and s["hours_known"] for s in stops)


def test_a_changed_input_is_refused(tmp_path):
    rows = city_rows(4)
    lis, enr, pho = world(tmp_path, rows)
    (lis / "listings.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="listings.jsonl does not match"):
        run((lis, enr, pho), tmp_path)


def test_a_different_country_filter_is_refused(tmp_path):
    folders = world(tmp_path, city_rows(4), country_filter="FR")
    with pytest.raises(PipelineError, match="country"):
        run(folders, tmp_path)


def test_a_missing_manifest_names_the_job_to_run(tmp_path):
    lis, enr, pho = world(tmp_path, city_rows(4))
    (pho / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="photo-select"):
        run((lis, enr, pho), tmp_path)


# --- damaged and odd input, found in review -------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"schedule": [{"days": ["mon"], "ranges": [["09:00"]]}]},
        {"schedule": [{"days": ["mon"], "ranges": [["", "17:00"]]}]},
        {"schedule": ["9AM"]},
        {"schedule": [{"days": ["mon"], "ranges": [["25:00", "26:00"]]}]},
        {"schedule": [{"days": ["mon"], "ranges": [["09:00", "09:00"]]}]},
    ],
)
def test_malformed_hours_count_as_unknown_not_a_crash(tmp_path, bad):
    rows = [listing_row(1, "A", hours_parsed=bad)]
    lis, enr, pho = world(tmp_path, rows)
    stops, _ = read_stops(lis, enr, pho, "IN", RULES)
    assert stops[0].hours is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(name=5),
        lambda r: r.update(quality_flags=7),
        lambda r: r.update(owner_page_id=None),
        lambda r: r.update(page_id=[1]),
    ],
)
def test_a_damaged_listing_row_is_one_clear_error(tmp_path, mutate):
    row = listing_row(1, "A")
    mutate(row)
    lis, enr, pho = world(tmp_path, [row], facts=[])
    with pytest.raises(PipelineError, match="listings.jsonl has a damaged row"):
        read_stops(lis, enr, pho, "IN", RULES)


def test_a_damaged_facts_row_is_one_clear_error(tmp_path):
    rows = [listing_row(1, "A")]
    lis, enr, pho = world(tmp_path, rows, facts=[{"ordinal": 1}])
    with pytest.raises(PipelineError, match="listing_facts.jsonl has a damaged row"):
        read_stops(lis, enr, pho, "IN", RULES)


def test_pipes_in_a_name_do_not_break_the_report_table(tmp_path):
    rows = [listing_row(i, f"S{i}", km_north=i * 0.2) for i in range(1, 4)]
    rows = [{**r, "owner_title": "Odd | City"} for r in rows]
    report = (run(world(tmp_path, rows), tmp_path).output_dir / "report.md").read_text("utf-8")
    assert "Odd \\| City" in report


# --- attraction sub-articles as stops (owner ruling 2026-11-14) -----------------------------

AGRA = (325, "agra", "Agra", "city")


def agra_world(tmp_path, **kw):
    rows = [listing_row(i, f"Fort {i}", km_north=i * 0.2, owner=AGRA) for i in range(1, 4)]
    articles = [
        article_row(325, "Agra", "city", "Uttar Pradesh"),
        article_row(900, "Agra/Taj Mahal", "district", "Agra"),
        article_row(901, "Agra/Old Quarter", "district", "Agra"),
        article_row(902, "Agra Wildlife Park", "park", "Agra"),
        article_row(903, "Agra Day Trip", "itinerary", "Agra"),
        article_row(904, "Agra Region Thing", "region", "Agra"),
        article_row(905, "Agra Village", "city", "Agra"),
        article_row(906, "Agra/Hidden Tomb", "district", "Agra"),
        article_row(907, "Agra/Fort 1", "district", "Agra"),
        article_row(908, "Agra/Bad Point", "district", "Agra"),
        article_row(909, "Elsewhere Falls", "park", "Some Region"),
        article_row(1, "Some Region", "region", "India"),
    ]
    dests = [
        dest_fact(900, "Agra/Taj Mahal", "sight", km_north=0.3, qid="Q9141"),
        dest_fact(901, "Agra/Old Quarter", "neighbourhood", km_north=0.4),
        dest_fact(902, "Agra Wildlife Park", "park_nature", km_north=0.5),
        dest_fact(903, "Agra Day Trip", "sight", km_north=0.1),
        dest_fact(904, "Agra Region Thing", "sight", km_north=0.1),
        dest_fact(905, "Agra Village", "park_nature", km_north=0.1),
        dest_fact(906, "Agra/Hidden Tomb", "sight", coord=False),
        dest_fact(907, "Agra/Fort 1", "fort_palace", km_north=0.2),
        dest_fact(908, "Agra/Bad Point", "sight", km_north=0.2, flags=["wikidata_coord_disagrees"]),
        dest_fact(909, "Elsewhere Falls", "park_nature", km_north=0.1),
    ]
    return world(
        tmp_path,
        rows,
        articles=articles,
        dest_facts=dests,
        photo_articles=[900],
        summaries=[("article", 900, 0)],
        **kw,
    )


def agra_stops(folders):
    lis, enr, pho = folders
    return read_stops(
        lis,
        enr,
        pho,
        "IN",
        RULES,
        discover_folder=lis.parent / "discover",
        summaries_folder=lis.parent / "summaries",
    )


def test_attraction_subarticles_become_stops_under_their_parent_city(tmp_path):
    stops, excluded = agra_stops(agra_world(tmp_path))
    by_name = {s.name: s for s in stops}
    assert sorted(by_name) == ["Agra Wildlife Park", "Fort 1", "Fort 2", "Fort 3", "Taj Mahal"]
    taj = by_name["Taj Mahal"]
    assert (taj.source, taj.owner_id, taj.owner_slug, taj.owner_title) == (
        "article",
        325,
        "agra",
        "Agra",
    )
    assert (taj.page_id, taj.ordinal, taj.kind, taj.section) == (900, 0, "sight", "see")
    assert taj.has_photo and taj.has_summary
    assert not by_name["Agra Wildlife Park"].has_photo
    assert not by_name["Fort 1"].has_summary and by_name["Fort 1"].source == "listing"
    assert {k: v for k, v in excluded.items() if k.startswith("subarticle_")} == {
        "subarticle_no_attraction_kind": 1,  # the neighbourhood
        "subarticle_type_skipped": 3,  # itinerary, region, city
        "subarticle_no_coordinates": 1,
        "subarticle_coord_disputed": 1,
        "subarticle_already_listed": 1,  # Agra/Fort 1 repeats the listing Fort 1
    }


def test_subarticles_that_repeat_a_listing_are_not_added_twice(tmp_path):
    rows = [
        listing_row(1, "Taj Mahal", owner=AGRA),
        listing_row(2, "Other", km_north=0.2, owner=AGRA),
    ]
    facts = [{**fact_row(rows[0]), "wikidata_id": "Q9141"}, fact_row(rows[1])]
    articles = [
        article_row(325, "Agra", "city", "Uttar Pradesh"),
        article_row(900, "Agra/Taj Mahal", "district", "Agra"),
        article_row(901, "Agra/Other Name", "district", "Agra"),
    ]
    dests = [
        dest_fact(900, "Agra/Taj Mahal", "sight", km_north=0.1, qid="Q9141"),
        dest_fact(901, "Agra/Other Name", "sight", km_north=0.2, qid="Q9141"),
    ]
    folders = world(tmp_path, rows, facts, articles=articles, dest_facts=dests)
    stops, excluded = agra_stops(folders)
    assert sorted(s.name for s in stops) == ["Other", "Taj Mahal"]
    assert excluded["subarticle_already_listed"] == 2


def test_the_taj_mahal_appears_in_the_blocks_and_the_report_counts_subarticles(tmp_path):
    out = run(agra_world(tmp_path), tmp_path).output_dir
    stops = [json.loads(x) for x in (out / "day_block_stops.jsonl").read_text("utf-8").splitlines()]
    taj = [s for s in stops if s["name"] == "Taj Mahal"]
    assert len(taj) == 1 and taj[0]["source"] == "article" and taj[0]["has_summary"]
    assert taj[0]["has_photo"] and taj[0]["page_id"] == 900 and taj[0]["ordinal"] == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"]["subarticle_stops"] == 2
    assert "sub-article" in (out / "report.md").read_text("utf-8").lower()


def test_listing_stops_carry_their_summary_flag(tmp_path):
    rows = city_rows(2)
    folders = write_inputs(
        tmp_path, rows, [fact_row(r) for r in rows], summaries=[("listing", 1, rows[0]["ordinal"])]
    )
    lis, enr, pho = folders
    stops, _ = read_stops(lis, enr, pho, "IN", RULES, summaries_folder=lis.parent / "summaries")
    assert [s.has_summary for s in stops] == [True, False]


def test_a_changed_articles_file_is_refused(tmp_path):
    folders = agra_world(tmp_path)
    (folders[0].parent / "discover" / "articles.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="articles.jsonl does not match"):
        run(folders, tmp_path)


def test_a_missing_summaries_manifest_names_the_job_to_run(tmp_path):
    folders = agra_world(tmp_path)
    (folders[0].parent / "summaries" / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="wikipedia-summary"):
        run(folders, tmp_path)


def test_block_stop_rows_carry_the_unesco_flag_and_basis(tmp_path):
    rows = city_rows(3)
    facts = [fact_row(rows[0], unesco=True), fact_row(rows[1]), fact_row(rows[2])]
    out = run(world(tmp_path, rows, facts), tmp_path).output_dir
    stops = [json.loads(x) for x in (out / "day_block_stops.jsonl").read_text("utf-8").splitlines()]
    unesco = [s for s in stops if s["unesco"]]
    assert len(unesco) == 1 and unesco[0]["duration_basis"] == "unesco"
    assert all(s["duration_basis"] == "default" for s in stops if not s["unesco"])
