import csv
import json
from datetime import date

import pytest
import respx

from catalogue.enrich.run import run_enrich_wikidata
from catalogue.errors import PipelineError
from catalogue.listings.quality import load_rules
from tests.apifakes import (
    coordinate_claim,
    entity,
    fake_wikidata_full,
    fake_wikipedia,
    fake_wikivoyage,
    item_claim,
    quantity_claim,
    string_claim,
    time_qualifier,
)
from tests.enrichfixture import article, listing, write_inputs

WIKIVOYAGE = "https://en.wikivoyage.org/w/api.php"
ENWIKI = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
TODAY = date(2026, 11, 12)
INDIA, PAKISTAN, TZ_ITEM, RUPEE, WHS = "Q668", "Q843", "Q9001", "Q9002", "Q9259"
FORT_CLASS = "Q9101"
KIND_MAP = {FORT_CLASS: "fort_palace"}

ARTICLES = [
    article(1, "Jaipur", qid="Q10"),
    article(2, "Abhaneri", role="destination_outline", status="outline"),
    article(3, "Nowhere", role="destination_outline", status="outline"),
    article(4, "Ghost", role="destination_outline", status="outline"),
    article(5, "India", type_="country", role="browse", qid=INDIA, status=None),
    article(6, "Palace itinerary", type_="itinerary", role="later_wave", status=None),
    article(7, "Badgeo", role="destination_outline", status="outline"),
]
PAGE_GEO = {2: ("", ""), 3: ("", ""), 4: ("", ""), 7: ("999", "10")}
DESTINATIONS = [{"wikivoyage_page_id": 1, "confirmation": "confirmed", "confirmation_reason": None}]

LISTINGS = [
    listing(1, 1, "Amber Fort", wikidata="Q100"),
    listing(1, 2, "City Palace", wikidata="Q101", lat="26.925", long="75.825"),
    listing(1, 3, "Far Temple", wikidata="Q102"),
    listing(1, 4, "Hawa Mahal", wikipedia="Hawa_Mahal"),
    listing(1, 5, "Mercury Temple", wikipedia="Mercury"),
    listing(1, 6, "Jaipur Itself", wikidata="Q10"),
    listing(1, 7, "Bad Id", wikidata="Q1x"),
    listing(1, 8, "Gone", wikidata="Q999"),
    listing(1, 9, "Plain Cafe", kind="eat", lat="26.9", long="75.8"),
    listing(1, 10, "Lost Shop", kind="buy"),
    listing(1, 11, "Merged", wikidata="Q50"),
]


def in_india(*claims):
    return {"P17": [item_claim(INDIA)], **dict(claims)}


def point(lat, lon):
    return ("P625", [coordinate_claim(lat, lon)])


FEE = quantity_claim("+100", RUPEE, qualifiers={"P585": time_qualifier("+2026-01-01T00:00:00Z")})

ENTITIES = {
    "Q10": entity(in_india(point(26.93, 75.83), ("P421", [item_claim(TZ_ITEM)])), label="Jaipur"),
    "Q20": entity(in_india(point(27.0, 76.6)), label="Abhaneri"),
    "Q30": entity({"P17": [item_claim(PAKISTAN)], **dict([point(27.0, 76.0)])}, label="Ghost"),
    "Q40": entity(in_india(point(27.0, 76.1)), label="Badgeo"),
    INDIA: entity({"P421": [item_claim(TZ_ITEM)]}, label="India"),
    "Q100": entity(
        in_india(
            point(26.99, 75.85),
            ("P31", [item_claim(FORT_CLASS)]),
            ("P1435", [item_claim(WHS)]),
            ("P757", [string_claim("247")]),
            ("P18", [string_claim("Amber.jpg")]),
            ("P2555", [FEE]),
        ),
        label="Amer Fort",
        description="fort in Jaipur",
        enwiki="Amer Fort",
        revid=555,
    ),
    "Q101": entity(in_india(point(26.926, 75.826)), label="City Palace"),
    "Q102": entity(in_india(point(10.0, 10.0)), label="Far Temple"),
    "Q103": entity(in_india(point(26.92, 75.83)), label="Hawa Mahal"),
    "Q51": entity(in_india(point(26.9, 75.8)), label="Merged target"),
    TZ_ITEM: entity({"P6687": [string_claim("Asia/Kolkata")]}, label="UTC+05:30"),
    RUPEE: entity({"P498": [string_claim("INR")]}, label="Indian rupee"),
    FORT_CLASS: entity({}, label="fort"),
    WHS: entity({}, label="World Heritage Site"),
}


def mock_all(entities=None, seen=None):
    respx.get(WIKIVOYAGE).mock(
        side_effect=fake_wikivoyage({"Abhaneri": "Q20", "Ghost": "Q30", "Badgeo": "Q40"})
    )
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia(
            {"Hawa Mahal": {"qid": "Q103"}, "Mercury": {"qid": "Q9", "disambiguation": True}}
        )
    )
    respx.get(WIKIDATA).mock(
        side_effect=fake_wikidata_full(
            ENTITIES if entities is None else entities, redirects={"Q50": "Q51"}, seen=seen
        )
    )


@pytest.fixture
def folders(tmp_path):
    return write_inputs(
        tmp_path / "in",
        articles=ARTICLES,
        listings=LISTINGS,
        destinations=DESTINATIONS,
        page_geo=PAGE_GEO,
    )


def run(client, folders, tmp_path, **kw):
    discover, listings = folders
    return run_enrich_wikidata(
        client,
        discover_folder=discover,
        listings_folder=listings,
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        quality_rules=load_rules(),
        kind_map=KIND_MAP,
        **kw,
    )


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def table(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def result(client, folders, tmp_path):
    with respx.mock:
        mock_all()
        return run(client, folders, tmp_path)


def destinations(result):
    rows = jsonl(result.output_dir / "destination_facts.jsonl")
    return {r["wikivoyage_page_id"]: r for r in rows}


def listings(result):
    rows = jsonl(result.output_dir / "listing_facts.jsonl")
    return {(r["page_id"], r["ordinal"]): r for r in rows}


def test_output_folder_carries_the_country_filter(result):
    assert result.output_dir.name == "20261101-IN"
    assert (result.output_dir / "manifest.json").is_file()


def test_destination_with_its_own_point_keeps_it_and_gets_a_zone(result):
    row = destinations(result)[1]
    assert (row["coord_source"], row["coord_filled"]) == ("wikivoyage", False)
    assert row["wikidata_id"] == "Q10" and row["wikidata_source"] == "discover"
    assert (row["timezone"], row["timezone_basis"]) == ("Asia/Kolkata", "wikidata_item")
    assert 0 < row["disagreement_km"] < 25 and row["flags"] == []


def test_outline_without_a_qid_gets_one_from_wikivoyage_and_a_wikidata_point(result):
    row = destinations(result)[2]
    assert row["wikidata_id"] == "Q20" and row["wikidata_source"] == "pageprops"
    assert row["coord_filled"] and row["coord_source"] == "wikidata"
    assert (row["coord_lat"], row["coord_long"]) == (27.0, 76.6)
    assert (row["timezone"], row["timezone_basis"]) == ("Asia/Kolkata", "country_single_zone")
    assert row["flags"] == []


def test_outline_without_any_item_is_flagged_and_left_empty(result):
    row = destinations(result)[3]
    assert not row["coord_filled"] and row["coord_lat"] is None
    assert row["flags"] == ["no_wikidata_id"] and row["wikidata_id"] is None


def test_outline_in_another_country_keeps_the_wikidata_point_but_is_not_filled(result):
    row = destinations(result)[4]
    assert not row["coord_filled"] and row["coord_lat"] is None
    assert (row["wikidata_lat"], row["wikidata_long"]) == (27.0, 76.0)
    assert row["flags"] == ["wikidata_country_disagrees"]


def test_an_invalid_own_geo_is_flagged_and_replaced_only_by_a_clean_point(result):
    row = destinations(result)[7]
    assert row["coord_filled"] and row["flags"] == ["own_geo_invalid"]


def test_other_roles_get_facts_but_no_coordinate_decision(result):
    country, itinerary = destinations(result)[5], destinations(result)[6]
    assert not country["coord_filled"] and country["flags"] == []
    assert itinerary["wikidata_id"] is None and itinerary["flags"] == []


def test_a_listing_with_a_clean_wikidata_point_is_filled_with_its_facts(result):
    row = listings(result)[(1, 1)]
    assert row["coord_filled"] and row["coord_source"] == "wikidata"
    assert (row["coord_lat"], row["coord_long"]) == (26.99, 75.85)
    assert row["wikidata_id"] == "Q100" and row["wikidata_source"] == "wikivoyage"
    assert row["is_unesco"] is True and row["kind"] == "fort_palace"
    assert row["image"] == "Amber.jpg" and row["label"] == "Amer Fort"
    assert row["description"] == "fort in Jaipur" and row["enwiki_title"] == "Amer Fort"
    assert row["wikidata_revision_id"] == 555 and row["flags"] == []
    assert row["licence"] == "CC0 1.0" and "Q100" in row["source_url"]
    assert "fee" not in json.dumps(row).lower()


def test_a_listing_with_its_own_point_keeps_it(result):
    row = listings(result)[(1, 2)]
    assert not row["coord_filled"] and row["coord_source"] == "wikivoyage"
    assert (row["coord_lat"], row["coord_long"]) == (26.925, 75.825)


def test_each_refusal_has_its_flag(result):
    rows = listings(result)
    flags = {key: r["flags"] for key, r in rows.items()}
    assert flags[(1, 3)] == ["wikidata_coord_far"]
    assert flags[(1, 5)] == ["no_wikidata_id"]
    assert flags[(1, 6)] == ["item_is_destination"]
    assert flags[(1, 7)] == ["bad_wikidata_id"]
    assert flags[(1, 8)] == ["item_missing"]
    assert flags[(1, 10)] == ["no_wikidata_id"]
    assert flags[(1, 11)] == ["item_merged"]
    filled = {k for k, r in rows.items() if r["coord_filled"]}
    assert filled == {(1, 1), (1, 4)}
    assert (rows[(1, 3)]["wikidata_lat"], rows[(1, 3)]["wikidata_long"]) == (10.0, 10.0)


def test_a_wikipedia_link_resolves_and_the_listing_is_filled(result):
    rows = listings(result)
    row = rows[(1, 4)]
    assert row["wikidata_id"] == "Q103" and row["wikidata_source"] == "wikipedia_link"
    assert row["wikipedia_status"] == "resolved" and row["coord_filled"]
    assert rows[(1, 5)]["wikipedia_status"] == "disambiguation"
    assert rows[(1, 5)]["wikidata_id"] is None


def test_listings_with_nothing_to_say_have_no_row(result):
    assert (1, 9) not in listings(result)  # no item, but it already has a point


def test_wikipedia_links_file_lists_every_outcome(result):
    rows = {r["listing_key"]: r for r in table(result.output_dir / "wikipedia_links.csv")}
    assert (
        rows["see:hawa-mahal"]["status"] == "resolved" and rows["see:hawa-mahal"]["qid"] == "Q103"
    )
    assert rows["see:mercury-temple"]["status"] == "disambiguation"


def test_coordinate_counts_before_and_after_by_country(result):
    rows = {
        (r["country_iso"], r["subject"]): r
        for r in table(result.output_dir / "coord_fill_by_country.csv")
    }
    listings_row = rows[("IN", "listings")]
    assert (listings_row["missing_before"], listings_row["filled"]) == ("9", "2")
    assert listings_row["filled_via_wikipedia_link"] == "1" and listings_row["still_missing"] == "7"
    destinations_row = rows[("IN", "destinations")]
    assert (destinations_row["missing_before"], destinations_row["filled"]) == ("4", "2")
    assert destinations_row["still_missing"] == "2"


def test_flag_counts_by_country(result):
    rows = table(result.output_dir / "flags_by_country.csv")
    counts = {(r["subject"], r["flag"]): int(r["count"]) for r in rows if r["country_iso"] == "IN"}
    assert counts[("listings", "wikidata_coord_far")] == 1
    assert counts[("destinations", "wikidata_country_disagrees")] == 1


def test_fee_statements_are_only_counted(result):
    rows = table(result.output_dir / "p2555_counts.csv")
    everything = next(r for r in rows if r["country_iso"] == "IN" and r["currency"] == "ALL")
    assert (everything["items_with_fee"], everything["dated"]) == ("1", "1")
    assert (everything["within_two_years"], everything["with_currency"]) == ("1", "1")
    inr = next(r for r in rows if r["currency"] == "INR")
    assert inr["items_with_fee"] == "1"
    samples = table(result.output_dir / "p2555_samples.csv")
    assert samples[0]["wikidata_id"] == "Q100" and samples[0]["currency"] == "INR"


def test_heritage_and_unmapped_class_reports(result):
    heritage = table(result.output_dir / "heritage_designations.csv")
    assert heritage == [
        {"country_iso": "IN", "designation_qid": WHS, "label": "World Heritage Site", "items": "1"}
    ]
    unmapped = table(result.output_dir / "kind_unmapped.csv")
    assert unmapped == []  # the only class in the data is mapped


def test_manifest_records_inputs_rules_and_output_hashes(result):
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["country_filter"] == "IN" and manifest["dump_date"] == "20261101"
    assert manifest["enrich_rules_version"] == 1 and manifest["quality_rules_version"] == 2
    assert len(manifest["listings_manifest_sha256"]) == 64
    assert set(manifest["outputs_sha256"]) >= {"destination_facts.jsonl", "listing_facts.jsonl"}
    assert manifest["totals"]["listings"]["filled"] == 2


def test_the_summary_has_counts_and_no_row_text(result):
    text = result.summary()
    assert "filled" in text and "Amber Fort" not in text and "example.invalid" not in text


def test_a_second_run_gives_identical_files(client, folders, tmp_path, result):
    first = (result.output_dir / "manifest.json").read_text(encoding="utf-8")
    with respx.mock:
        mock_all()
        again = run(client, folders, tmp_path)
    assert (again.output_dir / "manifest.json").read_text(encoding="utf-8") == first


def test_no_output_file_holds_the_contact_address(result):
    for path in result.output_dir.iterdir():
        assert "example.invalid" not in path.read_text(encoding="utf-8")


def test_requests_are_batched_sorted_and_the_second_pass_asks_for_referenced_items(
    client, folders, tmp_path
):
    seen: list[dict] = []
    with respx.mock:
        mock_all(seen=seen)
        run(client, folders, tmp_path)
    batches = [p["ids"].split("|") for p in seen]
    assert all(len(b) <= 50 for b in batches)
    asked = {q for b in batches for q in b}
    assert {TZ_ITEM, RUPEE, FORT_CLASS, WHS} <= asked
    assert "Q1x" not in asked


def test_a_changed_world_heritage_label_stops_the_run(client, folders, tmp_path):
    wrong = {**ENTITIES, WHS: entity({}, label="Something else")}
    with respx.mock:
        mock_all(entities=wrong)
        with pytest.raises(PipelineError, match="Q9259"):
            run(client, folders, tmp_path)


def test_a_missing_world_heritage_item_stops_the_run(client, folders, tmp_path):
    without = {k: v for k, v in ENTITIES.items() if k != WHS}
    with respx.mock:
        mock_all(entities=without)
        with pytest.raises(PipelineError, match="Q9259"):
            run(client, folders, tmp_path)


def test_stale_inputs_are_refused_before_any_request(client, folders, tmp_path):
    discover, _ = folders
    path = discover / "articles.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with respx.mock:
        route = respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({}))
        with pytest.raises(PipelineError):
            run(client, folders, tmp_path)
        assert not route.called


def test_an_unknown_detail_country_is_refused(client, folders, tmp_path):
    with respx.mock:
        mock_all()
        with pytest.raises(PipelineError, match="ZZ"):
            run(client, folders, tmp_path, detail_countries=["ZZ"])


def test_a_detail_report_is_written_for_the_country(client, folders, tmp_path):
    with respx.mock:
        mock_all()
        done = run(client, folders, tmp_path, detail_countries=["IN"])
    assert "India" in (done.output_dir / "detail-IN.md").read_text(encoding="utf-8")
    assert "Coordinates" in (done.output_dir / "report.md").read_text(encoding="utf-8")
