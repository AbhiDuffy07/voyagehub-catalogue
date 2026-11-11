"""B3b behaviour of `fetch-listings` that needs a whole (synthetic) run."""

import json

import pytest

from catalogue.discover.dump import sha1_of
from catalogue.discover.slugs import RegistryEntry, save_registry
from catalogue.listings.reference import CurrencyInfo, Reference
from catalogue.listings.run import run_fetch_listings
from tests.dumpfixture import page, write_dump
from tests.test_listings_run import ARTICLES, TODAY, article, jsonl

ALL_ARTICLES = [
    *ARTICLES,
    article(30, "Periyar", "park", "usable", ["Kerala", "India"], "India", "IN", "destination"),
]


def build(tmp_path, pages, articles=ALL_ARTICLES, reference=None, **kwargs):
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
