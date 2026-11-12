"""B3b behaviour of `fetch-listings` that needs a whole (synthetic) run."""

import json

import pytest

from catalogue.discover.dump import sha1_of
from catalogue.discover.slugs import RegistryEntry, save_registry
from catalogue.listings.quality import QualityRules
from catalogue.listings.reference import CurrencyInfo, Reference
from catalogue.listings.run import run_fetch_listings
from tests.dumpfixture import page, write_dump
from tests.test_listings_run import ARTICLES, TODAY, article, jsonl

ALL_ARTICLES = [
    *ARTICLES,
    article(30, "Periyar", "park", "usable", ["Kerala", "India"], "India", "IN", "destination"),
]


def build(tmp_path, pages, articles=ALL_ARTICLES, reference=None, rules=None, **kwargs):
    dump = write_dump(tmp_path / "dump.xml.bz2", pages)
    discover = tmp_path / "discover" / "20261101"
    discover.mkdir(parents=True)
    rows = sorted(articles, key=lambda r: r["wikivoyage_page_id"])
    (discover / "articles.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (discover / "manifest.json").write_text(json.dumps({"dump_sha1": sha1_of(dump)}))
    registry = tmp_path / "data" / "slugs.json"
    save_registry(registry, {3: RegistryEntry("kochi", "Kochi")})
    return run_fetch_listings(
        dump_path=dump,
        dump_date="20261101",
        discover_folder=discover,
        out_dir=tmp_path / "out",
        registry_path=registry,
        today=TODAY,
        reference=reference if reference is not None else Reference(),
        rules=rules if rules is not None else QualityRules(version=1, far_km={}),
        **kwargs,
    )


def rows(result, name="listings.jsonl"):
    return jsonl(result.output_dir / name)


@pytest.fixture
def unicode_run(tmp_path):
    pages = [
        page(
            "Kochi",
            "== See ==\n{{see|name=清水寺|lat=1|long=2}}\n{{see|name=Café Zürich}}",
            page_id=3,
        )
    ]
    return build(tmp_path, pages)


def test_keys_keep_unicode_names_and_rows_carry_the_key_version(unicode_run):
    keys = {r["name"]: (r["listing_key"], r["key_version"]) for r in rows(unicode_run)}

    assert keys == {"清水寺": ("see:清水寺", 2), "Café Zürich": ("see:café-zürich", 2)}


MARKER_PAGES = [
    page(
        "Kochi",
        "== Go ==\n{{marker|type=go|name=Bus stand|lat=9.97|long=76.28|wikidata=Q5}}\n"
        "{{marker|name=No type|lat=1|long=2}}\n{{marker|type=city|name=Elsewhere}}\n"
        "{{marker|type=Eat|name=Tea stall}}\n== See ==\n{{see|name=Palace}}",
        page_id=3,
    ),
    page(
        "Fort Kochi",
        "== Do ==\n{{marker|type=do|name=Net watching|lat=9.96|long=76.24}}",
        page_id=4,
    ),
    page("Periyar", "== See ==\n{{marker|type=see|name=Lake view|lat=9.5|long=77.1}}", page_id=30),
    page("Kerala", "== Cities ==\n{{marker|type=see|name=Region pin|lat=9|long=76}}", page_id=2),
    page("Tea tour", "== Day 1 ==\n{{marker|type=see|name=Itinerary pin}}", page_id=12),
]


@pytest.fixture
def marker_run(tmp_path):
    return build(tmp_path, MARKER_PAGES)


def test_typed_markers_on_city_district_and_park_pages_become_listings(marker_run):
    by_name = {r["name"]: r for r in rows(marker_run)}

    assert {"Bus stand", "Tea stall", "Net watching", "Lake view"} <= set(by_name)
    bus = by_name["Bus stand"]
    assert (bus["kind"], bus["from_marker"], bus["section_key"]) == ("go", True, "go")
    assert (bus["lat"], bus["long"], bus["wikidata"]) == ("9.97", "76.28", "Q5")
    assert (bus["address"], bus["hours"], bus["price"]) == ("", "", "")
    assert bus["listing_key"] == "go:bus-stand"
    assert by_name["Tea stall"]["kind"] == "eat"  # the type is case-insensitive
    assert by_name["Palace"]["from_marker"] is False


def test_a_district_marker_rolls_up_to_its_city_like_any_listing(marker_run):
    net = next(r for r in rows(marker_run) if r["name"] == "Net watching")

    assert (net["via_district"], net["owner_title"], net["page_type"]) == (
        True,
        "Kochi",
        "district",
    )


def test_other_markers_stay_in_markers_jsonl_and_are_not_listings(marker_run):
    listing_names = {r["name"] for r in rows(marker_run)}
    marker_names = {r["name"] for r in rows(marker_run, "markers.jsonl")}

    assert marker_names == {"No type", "Elsewhere", "Region pin", "Itinerary pin"}
    assert not listing_names & marker_names


def test_marker_listings_are_counted_as_listings_not_as_markers(marker_run):
    totals = marker_run.totals

    assert totals["listings"] == 5 and totals["markers"] == 4
    assert totals["marker_listings"] == 4
    assert totals["by_kind"] == {"go": 1, "eat": 1, "see": 2, "do": 1}


def test_marker_listings_are_in_the_owner_counts(marker_run):
    assert marker_run.owner_counts[3] == {"own": 3, "from_districts": 1}


# --- prices, hours and lastedit in the rows ------------------------------------------------------

REFERENCE = Reference(
    country_currency={"IN": CurrencyInfo("INR", 2)}, demonyms={"IN": frozenset({"indian"})}
)

READ_PAGES = [
    page(
        "Kochi",
        "== See ==\n"
        "{{see|name=Palace|price=Adult: Rs 10, Foreigner: Rs 100|hours=Mon-Fri 9AM-5PM"
        "|lastedit=2021-03-04}}\n"
        "{{see|name=Fort|price=Varies|hours=9-5PM|lastedit=Octo}}\n"
        "{{see|name=Park|price=Rs 5|lastedit=2026-05}}\n"
        "{{see|name=Beach|lastedit=2024}}\n"
        "{{see|name=Plain}}",
        page_id=3,
    )
]


@pytest.fixture
def read_run(tmp_path):
    return build(tmp_path, READ_PAGES, reference=REFERENCE)


def by_name(result):
    return {r["name"]: r for r in rows(result)}


def test_the_raw_price_hours_and_lastedit_stay_and_the_parsed_fields_are_added(read_run):
    palace = by_name(read_run)["Palace"]

    assert palace["price"] == "Adult: Rs 10, Foreigner: Rs 100"
    assert palace["price_status"] == "parsed"
    assert [(e["audience"], e["amount_minor"]) for e in palace["price_entries"]] == [
        ("adult", 1000),
        ("foreigner", 10000),
    ]
    assert (palace["price_is_free"], palace["price_tier"], palace["price_reason"]) == (
        False,
        None,
        None,
    )
    assert palace["hours"] == "Mon-Fri 9AM-5PM" and palace["hours_status"] == "parsed"
    assert palace["hours_parsed"]["schedule"][0]["days"] == ["mon", "tue", "wed", "thu", "fri"]
    assert palace["lastedit"] == "2021-03-04"
    assert (palace["lastedit_status"], palace["lastedit_precision"], palace["lastedit_year"]) == (
        "parsed",
        "day",
        2021,
    )


def test_unreadable_values_keep_their_raw_text_and_say_why(read_run):
    fort = by_name(read_run)["Fort"]

    assert (fort["price_status"], fort["price_reason"], fort["price_entries"]) == (
        "unparsed",
        "no_amount",
        [],
    )
    assert (fort["hours_status"], fort["hours_parsed"]) == ("raw_only", None)
    assert (fort["lastedit_status"], fort["lastedit_date"], fort["lastedit"]) == (
        "unparsed",
        None,
        "Octo",
    )


def test_empty_values_are_empty_not_missing_fields(read_run):
    plain = by_name(read_run)["Plain"]

    assert (plain["price_status"], plain["hours_status"], plain["lastedit_status"]) == (
        "empty",
        "empty",
        "missing",
    )
    assert plain["price_entries"] == [] and plain["hours_parsed"] is None


def test_the_freshness_of_priced_and_hours_listings_is_counted_for_the_two_year_decision(read_run):
    totals = read_run.totals

    # priced: Palace (2021, older), Fort (Octo, undated), Park (2026-05, within)
    assert totals["price_age"] == {"older": 1, "undated": 1, "within": 1}
    # with hours: Palace (older), Fort (undated)
    assert totals["hours_age"] == {"older": 1, "undated": 1}
    assert totals["price_status"] == {"parsed": 2, "unparsed": 1, "empty": 2}
    assert totals["hours_status"] == {"parsed": 1, "raw_only": 1, "empty": 3}
    assert totals["lastedit_status"] == {"parsed": 3, "unparsed": 1, "missing": 1}


def test_a_year_that_straddles_the_two_year_cutoff_is_counted_as_unclear(tmp_path):
    pages = [page("Kochi", "== See ==\n{{see|name=Beach|price=Rs 5|lastedit=2024}}", page_id=3)]

    result = build(tmp_path, pages, reference=REFERENCE)

    assert result.totals["price_age"] == {"unclear": 1}


def test_unparsed_and_partial_prices_are_sampled_for_review(read_run):
    text = (read_run.output_dir / "prices_unparsed.csv").read_text(encoding="utf-8")

    assert text.splitlines()[0] == "reason,page_id,listing_key,price"
    assert "no_amount,3,see:fort,Varies" in text
    assert "Palace" not in text


def test_the_reports_show_the_freshness_numbers_for_the_two_year_decision(tmp_path):
    result = build(tmp_path, READ_PAGES, reference=REFERENCE, detail_countries=["IN"])
    world = (result.output_dir / "report.md").read_text(encoding="utf-8")
    india = (result.output_dir / "detail-IN.md").read_text(encoding="utf-8")

    for text in (world, india):
        assert "## Prices: how old is the last edit" in text
        assert "| within 2 years | 1 |" in text
        assert "| older | 1 |" in text
        assert "| undated (missing, unreadable or implausible) | 1 |" in text
        assert "## Hours: how old is the last edit" in text
    assert "| unclear (the year straddles the cutoff) | 0 |" in india


# --- distance from the page's own geo ---------------------------------------------------------

GEO_PAGES = [
    page(
        "Kochi",
        "{{geo|9.9312|76.2673}}\n== See ==\n{{see|name=Near|lat=9.9400|long=76.2700}}\n"
        "{{see|name=Far|lat=10.5|long=77.1}}\n{{see|name=NoCoords}}\n{{see|name=Bad|lat=x|long=y}}",
        page_id=3,
    ),
    page("Fort Kochi", "== See ==\n{{see|name=NoPageGeo|lat=9.96|long=76.24}}", page_id=4),
]


def test_each_listing_carries_the_pages_geo_and_its_distance_from_it(tmp_path):
    result = build(tmp_path, GEO_PAGES)
    row = by_name(result)

    assert (row["Near"]["page_lat"], row["Near"]["page_long"]) == ("9.9312", "76.2673")
    assert 0.5 < row["Near"]["coord_distance_km"] < 1.5
    assert 100 < row["Far"]["coord_distance_km"] < 150
    for name in ("NoCoords", "Bad", "NoPageGeo"):
        assert row[name]["coord_distance_km"] is None, name
    assert (row["NoPageGeo"]["page_lat"], row["NoPageGeo"]["page_long"]) == ("", "")


def test_the_reports_show_distance_percentiles_by_page_type(tmp_path):
    result = build(tmp_path, GEO_PAGES, detail_countries=["IN"])

    for name in ("report.md", "detail-IN.md"):
        text = (result.output_dir / name).read_text(encoding="utf-8")
        assert "## Distance from the page's own geo, by page type" in text
        assert "| city | 2 |" in text


# --- quality flags, duplicates and the Wikidata gap ------------------------------------------

QUALITY_PAGES = [
    page(
        "Kochi",
        "{{geo|9.9312|76.2673}}\n== See ==\n"
        "{{see|name=Near|lat=9.94|long=76.27}}\n"
        "{{see|name=Far|lat=10.9|long=77.5}}\n"
        "{{see|name=Nowhere|wikidata=Q5}}\n"
        "{{see|name=Linked|wikipedia=Linked}}\n"
        "{{see|name=Nothing}}\n"
        "{{see|name=Bad|lat=x|long=y}}\n"
        "{{see|name=Web|lat=9.94|long=76.27|url=www.example.org|email=nope}}\n"
        "{{see|name=[[Marked]]|lat=9.94|long=76.27}}\n"
        "{{see|name=Palace|wikidata=Q9|lat=9.93|long=76.26}}\n"
        "{{see|name=Fort|wikidata=Q7|lat=9.93|long=76.26}}\n"
        "== Eat ==\n{{eat|name=Fort Cafe|wikidata=Q7|lat=9.93|long=76.26}}",
        page_id=3,
    ),
    page(
        "Fort Kochi",
        "== See ==\n{{see|name=Palace|wikidata=Q9|lat=9.96|long=76.24}}",
        page_id=4,
    ),
]
QUALITY_RULES = QualityRules(version=7, far_km={"city": 100.0})


@pytest.fixture
def quality_run(tmp_path):
    return build(tmp_path, QUALITY_PAGES, rules=QUALITY_RULES)


def test_every_listing_has_quality_flags_in_a_fixed_order(quality_run):
    row = by_name(quality_run)

    assert row["Near"]["quality_flags"] == []
    assert row["Far"]["quality_flags"] == ["coord_far"]
    assert row["Nowhere"]["quality_flags"] == ["coord_missing"]
    assert row["Bad"]["quality_flags"] == ["coord_invalid"]
    assert row["Web"]["quality_flags"] == ["url_invalid", "email_invalid"]
    assert row["[[Marked]]"]["quality_flags"] == ["name_markup"]


def test_a_page_without_a_usable_geo_leaves_the_coordinates_unchecked(quality_run):
    second_palace = next(r for r in rows(quality_run) if r["page_id"] == 4)

    assert "coord_unchecked" in second_palace["quality_flags"]
    assert second_palace["coord_distance_km"] is None


def test_listings_without_coordinates_are_grouped_by_what_could_fill_them(quality_run):
    row = by_name(quality_run)

    assert row["Nowhere"]["coord_gap"] == "has_wikidata"
    assert row["Linked"]["coord_gap"] == "wikipedia_only"
    assert row["Nothing"]["coord_gap"] == "neither"
    assert row["Bad"]["coord_gap"] == "neither"
    assert row["Near"]["coord_gap"] is None


def test_the_gap_is_counted_per_country_type_and_city(quality_run):
    text = (quality_run.output_dir / "wikidata_gap.csv").read_text(encoding="utf-8").splitlines()

    assert (
        text[0] == "country_iso,dimension,value,no_coordinates,has_wikidata,wikipedia_only,neither"
    )
    assert "IN,country,India,4,1,1,2" in text
    assert "IN,type,see,4,1,1,2" in text
    assert "IN,city,Kochi,4,1,1,2" in text


def test_a_later_duplicate_points_at_the_earlier_listing_across_a_city_and_its_district(
    quality_run,
):
    palaces = [r for r in rows(quality_run) if r["name"] == "Palace"]
    first, second = palaces

    assert (first["duplicate_of"], first["duplicate_basis"]) == (None, None)
    assert second["duplicate_of"] == first["listing_key"]
    assert second["duplicate_basis"] == "wikidata"
    assert "duplicate" in second["quality_flags"]
    assert first["listing_key"] != second["listing_key"]


def test_the_same_place_under_two_kinds_is_linked_but_not_a_duplicate(quality_run):
    row = by_name(quality_run)

    assert row["Fort Cafe"]["same_place_as"] == row["Fort"]["listing_key"]
    assert row["Fort Cafe"]["duplicate_of"] is None
    assert row["Fort"]["same_place_as"] is None


def test_the_flag_counts_and_the_rules_version_reach_the_totals_and_the_manifest(quality_run):
    flags = quality_run.totals["quality_flags"]

    assert flags["coord_far"] == 1 and flags["coord_missing"] == 3 and flags["coord_invalid"] == 1
    assert flags["url_invalid"] == 1 and flags["email_invalid"] == 1 and flags["name_markup"] == 1
    assert flags["duplicate"] == 1
    assert quality_run.totals["duplicates_by_basis"] == {"wikidata": 1}
    manifest = json.loads((quality_run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_rules_version"] == 7 and manifest["key_version"] == 2


def test_duplicates_are_sampled_for_review(quality_run):
    text = (quality_run.output_dir / "duplicates.csv").read_text(encoding="utf-8").splitlines()

    assert text[0] == "basis,owner,kind,name,listing_key,duplicate_of"
    assert len(text) == 2 and text[1].startswith("wikidata,Kochi,see,Palace,")


def test_the_reports_list_the_flags_and_the_wikidata_gap(tmp_path):
    result = build(tmp_path, QUALITY_PAGES, rules=QUALITY_RULES, detail_countries=["IN"])

    for name in ("report.md", "detail-IN.md"):
        text = (result.output_dir / name).read_text(encoding="utf-8")
        assert "## Quality flags" in text
        assert "| coord_far | 1 |" in text
        assert "## Listings without coordinates: what could fill them in" in text
        assert "| has a Wikidata ID | 1 |" in text
        assert "| a Wikipedia link only | 1 |" in text
        assert "| neither | 2 |" in text


def test_a_malformed_dump_date_is_one_clear_error():
    from catalogue.errors import PipelineError
    from catalogue.listings.run import parse_dump_date

    assert parse_dump_date("20261101").isoformat() == "2026-11-01"
    for bad in ("2026-11", "20261301", "abcdefgh", ""):
        with pytest.raises(PipelineError):
            parse_dump_date(bad)


def test_the_same_place_link_is_also_a_flag_on_the_row(quality_run):
    row = by_name(quality_run)

    assert "same_place_as" in row["Fort Cafe"]["quality_flags"]
    assert quality_run.totals["quality_flags"]["same_place_as"] == 1


def test_a_marker_in_an_empty_lead_still_becomes_a_listing(tmp_path):
    marker = "{{marker|type=see|name=Lonely Fort|lat=1|long=2}}"
    pages = [page("Kochi", "{{pagebanner}}" + marker, page_id=3)]

    result = build(tmp_path, pages)

    assert [r["name"] for r in rows(result)] == ["Lonely Fort"]


def test_review_csv_cells_cannot_start_a_spreadsheet_formula():
    from catalogue.listings.run import _safe_cell

    assert _safe_cell("=SUM(A1)") == "'=SUM(A1)"
    assert _safe_cell("+1") == "'+1" and _safe_cell("@x") == "'@x"
    assert _safe_cell("Palace") == "Palace" and _safe_cell(5) == 5
