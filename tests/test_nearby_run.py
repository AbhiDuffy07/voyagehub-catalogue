import json
from dataclasses import replace

import pytest
import respx

from catalogue.errors import PipelineError
from catalogue.nearby.inputs import verify_nearby
from catalogue.nearby.rules import load_nearby_rules
from catalogue.nearby.run import EXTRA_ORDINAL_BASE, run_nearby
from tests.nearbyfixture import (
    COMMONS,
    HAWA,
    WIKIDATA,
    build,
    clock_by_requests,
    fake_commons_api,
    fake_wikidata_api,
    geo_on,
    jsonl,
    listing,
    north,
    sha,
    write_manifest,
)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run(client, folders, wd_seen=None, cm_seen=None, **kwargs):
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_api(wd_seen))
    respx.get(COMMONS).mock(side_effect=fake_commons_api(cm_seen))
    kwargs.setdefault("rules", geo_on())
    return run_nearby(client, **folders, sleep=lambda s: None, **kwargs)


@respx.mock
def test_extra_places_come_from_wikidata_with_source_and_no_invented_values(client, tmp_path):
    result = run(client, build(tmp_path))
    places = rows(result.output_dir / "nearby_places.jsonl")
    assert [p["name"] for p in places] == ["Albert Hall Museum", "Galta Ji"]
    albert = places[0]
    assert albert["page_id"] == 1 and albert["owner_page_id"] == 1
    assert albert["ordinal"] == EXTRA_ORDINAL_BASE + 2001
    assert albert["listing_key"] == "see:albert-hall-museum~Q2001"
    assert albert["kind"] == "see" and albert["place_kind"] == "museum"
    assert albert["wikidata"] == "Q2001"
    assert albert["source"] == "wikidata_nearby"
    assert albert["source_url"] == "https://www.wikidata.org/wiki/Q2001"
    assert albert["licence"] == "CC0 1.0" and albert["attribution"] == "Wikidata contributors"
    assert albert["licence_url"].startswith("https://creativecommons.org/publicdomain/zero/")
    assert albert["duplicate_of"] is None and albert["country_iso"] == "IN"
    assert albert["price"] == "" and albert["price_status"] == "empty"
    assert albert["price_entries"] == [] and albert["price_tier"] is None
    assert albert["hours"] == "" and albert["hours_status"] == "empty"
    assert albert["hours_parsed"] is None
    assert {"wikidata_nearby", "price_unknown", "hours_unknown"} <= set(albert["quality_flags"])
    assert float(albert["lat"]) > 26.92 and albert["distance_from_centre_km"] == pytest.approx(
        1.0, abs=0.01
    )


@respx.mock
def test_items_that_are_not_new_visitor_places_are_skipped_and_counted(client, tmp_path):
    result = run(client, build(tmp_path))
    skipped = result.totals["places_skipped"]
    assert skipped == {
        "destination": 1,  # Amer's own item
        "same_as_listing": 1,  # Hawa Mahal is already a listing
        "kind": 1,  # a hotel
        "nearer_other_city": 1,  # Nahargarh is nearer Amer's centre
        "photo_name_word": 1,  # a collage as the only image
        "no_image": 1,
        "other_country": 1,
    }


@respx.mock
def test_geosearch_finds_a_named_photo_and_skips_the_busy_point_and_the_small_file(
    client, tmp_path
):
    band = replace(geo_on(), name_free_radius_m=40)
    result = run(client, build(tmp_path), rules=band)
    candidates = rows(result.output_dir / "nearby_candidates.jsonl")
    geo = [c for c in candidates if c["sources"] == ["commons_geosearch"]]
    assert [(c["page_id"], c["ordinal"], c["commons_file"]) for c in geo] == [
        (1, 1, "Hawa Mahal16.jpg")
    ]
    row = geo[0]
    assert row["usable"] is True and row["flags"] == []
    assert row["nearby_distance_m"] == 0.0 and row["nearby_named"] is True
    assert row["subject_name"] == "Hawa Mahal" and row["listing_key"] == "see:hawa-mahal"
    assert row["attribution"] == "Ann & Co, CC BY-SA 4.0, via Wikimedia Commons"
    assert result.totals["geosearch_skipped"]["busy_point"] == 4
    assert result.totals["photos_failed"]["below_gate"] == 1


@respx.mock
def test_every_extra_place_has_its_own_p18_candidate(client, tmp_path):
    result = run(client, build(tmp_path))
    p18 = [
        c
        for c in rows(result.output_dir / "nearby_candidates.jsonl")
        if c["sources"] == ["wikidata_p18"]
    ]
    assert sorted(c["commons_file"] for c in p18) == ["Albert Hall.jpg", "Galta Ji.jpg"]
    assert all(c["ordinal"] >= EXTRA_ORDINAL_BASE and c["usable"] for c in p18)
    assert all(c["nearby_distance_m"] is None for c in p18)


@respx.mock
def test_cities_that_already_have_enough_places_cost_no_request(client, tmp_path):
    wd_seen, cm_seen = [], []
    run(client, build(tmp_path), wd_seen, cm_seen)
    geosearches = [p["gscoord"] for p in wd_seen + cm_seen if p.get("list") == "geosearch"]
    assert len(geosearches) == 2  # Jaipur's centre and Hawa Mahal, nothing for Amer
    assert all("maxlag" in p for p in wd_seen + cm_seen)
    assert {p["gsradius"] for p in cm_seen if p.get("list") == "geosearch"} == {"100"}


@respx.mock
def test_a_rerun_answers_the_searches_from_the_cache(client, tmp_path):
    folders = build(tmp_path)
    run(client, folders)
    wd_seen, cm_seen = [], []
    result = run(client, folders, wd_seen, cm_seen)
    assert not [p for p in wd_seen + cm_seen if p.get("list") == "geosearch"]
    assert not [p for p in wd_seen if p.get("action") == "wbgetentities"]
    assert result.totals["cache"]["hits"] >= 3


@respx.mock
def test_a_world_run_works_for_any_country(client, tmp_path):
    result = run(client, build(tmp_path, world=True))
    geo = [
        c
        for c in rows(result.output_dir / "nearby_candidates.jsonl")
        if c["sources"] == ["commons_geosearch"]
    ]
    assert {(c["country_iso"], c["commons_file"]) for c in geo} == {
        ("IN", "Hawa Mahal16.jpg"),
        ("US", "Fenway Park 2010.jpg"),
    }
    assert result.output_dir.name == "20261101"


@respx.mock
def test_name_match_always_required_when_the_name_free_band_is_zero(client, tmp_path):
    strict = replace(geo_on(), name_free_radius_m=0)
    result = run(client, build(tmp_path), rules=strict)
    assert result.totals["geosearch_skipped"]["no_name_match"] == 4
    assert result.totals["geosearch_skipped"].get("busy_point", 0) == 0


@respx.mock
def test_a_place_with_a_disputed_or_copied_point_is_not_searched(client, tmp_path):
    folders = build(tmp_path)
    listings = rows(folders["listings_folder"] / "listings.jsonl")
    listings[0]["quality_flags"] = ["own_geo_invalid"]
    _rewrite_listings(folders, listings)
    cm_seen = []
    result = run(client, folders, cm_seen=cm_seen)
    assert not [p for p in cm_seen if p.get("list") == "geosearch"]
    assert result.totals["subjects_skipped"]["coord_disputed"] == 1


@respx.mock
def test_three_names_on_one_point_are_not_trusted(client, tmp_path):
    folders = build(tmp_path)
    listings = rows(folders["listings_folder"] / "listings.jsonl")
    listings += [
        listing(1, 8, 1, "Copy one", point=HAWA),
        listing(1, 9, 1, "Copy two", point=HAWA),
    ]
    _rewrite_listings(folders, listings)
    result = run(client, folders)
    assert result.totals["subjects_skipped"]["coord_shared"] == 3


@respx.mock
def test_a_wikivoyage_ordinal_in_the_reserved_range_stops_the_job(client, tmp_path):
    folders = build(tmp_path)
    listings = rows(folders["listings_folder"] / "listings.jsonl")
    listings.append(listing(1, EXTRA_ORDINAL_BASE, 1, "Huge", point=north(HAWA, 900)))
    _rewrite_listings(folders, listings)
    with pytest.raises(PipelineError) as raised:
        run(client, folders)
    assert "ordinal" in str(raised.value)


@respx.mock
def test_a_country_mismatch_is_refused(client, tmp_path):
    folders = build(tmp_path)
    with pytest.raises(PipelineError):
        run(client, {**folders, "country_iso": "US"})


@respx.mock
def test_the_manifest_records_inputs_rules_and_outputs(client, tmp_path):
    folders = build(tmp_path)
    result = run(client, folders)
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["country_filter"] == "IN"
    assert manifest["nearby_rules_version"] == load_nearby_rules().version
    assert manifest["photo_candidates_manifest_sha256"] == sha(
        folders["photos_folder"] / "manifest.json"
    )
    assert set(manifest["inputs_sha256"]) == {
        "articles.jsonl",
        "listings.jsonl",
        "destination_facts.jsonl",
        "listing_facts.jsonl",
        "photo_selection.jsonl",
        "summaries.jsonl",
    }
    assert manifest["cities_not_reached"] == []
    for name, digest in manifest["outputs_sha256"].items():
        assert sha(result.output_dir / name) == digest
    assert manifest["totals"]["short_cities"] == 1
    report = (result.output_dir / "report.md").read_text(encoding="utf-8")
    assert "example.invalid" not in report


def _rewrite_listings(folders, listings):
    lis = folders["listings_folder"]
    manifest = json.loads((lis / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["listings.jsonl"] = jsonl(lis / "listings.jsonl", listings)
    write_manifest(lis, manifest)


def _busy_first(answer, times, seen):
    """Answer `cirrussearch-too-busy-error` to the first `times` geosearches, then normally."""
    import httpx

    def wrapped(request):
        if request.url.params.get("list") == "geosearch" and len(seen) < times:
            seen.append(1)
            return httpx.Response(200, json={"error": {"code": "cirrussearch-too-busy-error"}})
        return answer(request)

    return wrapped


@respx.mock
def test_a_busy_search_is_waited_out_and_retried(client, tmp_path, capsys):
    seen, waits = [], []
    respx.get(WIKIDATA).mock(side_effect=_busy_first(fake_wikidata_api(), 2, seen))
    respx.get(COMMONS).mock(side_effect=fake_commons_api())
    result = run_nearby(client, **build(tmp_path), sleep=waits.append)
    assert result.totals["extra_places"] == 2
    assert waits[:2] == [5.0, 10.0]
    assert result.totals["searches_busy"] == 0
    busy_lines = [ln for ln in capsys.readouterr().out.splitlines() if "busy" in ln]
    assert busy_lines == [
        "www.wikidata.org search is too busy; waiting 5 s (retry 1)",
        "www.wikidata.org search is too busy; waiting 10 s (retry 2)",
    ]


@respx.mock
def test_a_search_that_stays_busy_is_skipped_counted_and_not_cached(client, tmp_path):
    seen = []
    folders = build(tmp_path)
    respx.get(WIKIDATA).mock(side_effect=_busy_first(fake_wikidata_api(), 99, seen))
    respx.get(COMMONS).mock(side_effect=fake_commons_api())
    result = run_nearby(client, **folders, sleep=lambda s: None, rules=geo_on())
    assert result.totals["extra_places"] == 0
    assert result.totals["searches_busy"] == 1
    assert result.totals["places_skipped"]["city_search_busy"] == 1
    # The Commons search for Hawa Mahal still ran (Commons was not busy).
    assert result.totals["subjects_with_candidate"] == 1
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_api())
    again = run_nearby(client, **folders, sleep=lambda s: None)
    assert again.totals["extra_places"] == 2  # a rerun fills it


@respx.mock
def test_searches_run_one_at_a_time(client, tmp_path, monkeypatch):
    from catalogue.nearby import run as nearby_run

    assert nearby_run.SEARCH_WORKERS == 1


@respx.mock
def test_the_city_name_in_a_place_name_is_no_name_match(client, tmp_path):
    """A listing named after its city ("Jaipur Gate" in Jaipur) must not accept every Jaipur photo:
    on the India sample "Omkareshwar Shiva Temple" took a river view named "...in Omkareshwar"."""
    from tests import nearbyfixture as nf

    folders = build(tmp_path)
    listings = rows(folders["listings_folder"] / "listings.jsonl")
    gate = nf.north(nf.HAWA, 700)
    listings.append(listing(1, 5, 1, "Jaipur Gate", point=gate))
    _rewrite_listings(folders, listings)
    near = {
        **nf.COMMONS_NEAR,
        gate: [
            ("File:Jaipur market 1.jpg", nf.north(gate, 60)),
            ("File:Jaipur Gate at dusk.jpg", nf.north(gate, 70)),
        ],
    }
    files = {
        **nf.FILES,
        "File:Jaipur market 1.jpg": nf.commons_file(sha1="m" * 40),
        "File:Jaipur Gate at dusk.jpg": nf.commons_file(sha1="g" * 40),
    }
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_api())
    respx.get(COMMONS).mock(side_effect=fake_commons_api(files=files, near=near))
    result = run_nearby(client, **folders, sleep=lambda s: None, rules=geo_on())
    gate_rows = [
        c
        for c in rows(result.output_dir / "nearby_candidates.jsonl")
        if (c["page_id"], c["ordinal"]) == (1, 5)
    ]
    assert [c["commons_file"] for c in gate_rows] == ["Jaipur Gate at dusk.jpg"]


def test_the_committed_rules_always_require_a_name_match():
    # Owner rule (2026-11-23): over 10% wrong in the 40-photo India sample sets the band to 0.
    assert load_nearby_rules().name_free_radius_m == 0


@respx.mock
def test_a_duplicate_listing_does_not_make_the_original_ambiguous(client, tmp_path):
    folders = build(tmp_path)
    listings = rows(folders["listings_folder"] / "listings.jsonl")
    listings.append(listing(1, 6, 1, "Hawa Mahal", point=HAWA, duplicate_of="see:hawa-mahal"))
    _rewrite_listings(folders, listings)
    result = run(client, folders)
    geo = [
        c["commons_file"]
        for c in rows(result.output_dir / "nearby_candidates.jsonl")
        if c["sources"] == ["commons_geosearch"]
    ]
    assert geo == ["Hawa Mahal16.jpg"]
    assert result.totals["geosearch_skipped"].get("ambiguous", 0) == 0


def test_the_committed_rules_keep_commons_geosearch_off():
    # Owner ruling (2026-11-25): with a name match always required, the India sample still had
    # 5 of 40 wrong, 4 of them from geosearch. Extra places from Wikidata stay on.
    assert load_nearby_rules().geosearch_enabled is False


@respx.mock
def test_with_geosearch_off_no_commons_search_runs_and_extra_places_still_come(client, tmp_path):
    wd_seen, cm_seen = [], []
    off = replace(geo_on(), geosearch_enabled=False)
    result = run(client, build(tmp_path), wd_seen, cm_seen, rules=off)
    assert not [p for p in cm_seen if p.get("list") == "geosearch"]
    candidates = rows(result.output_dir / "nearby_candidates.jsonl")
    assert not [c for c in candidates if "commons_geosearch" in c["sources"]]
    assert sorted(c["commons_file"] for c in candidates) == ["Albert Hall.jpg", "Galta Ji.jpg"]
    assert result.totals["subjects"] == 0
    # Hawa Mahal and Jantar Mantar: every place without a photo is skipped for the same reason.
    assert result.totals["subjects_skipped"] == {"geosearch_off": 2}
    assert "Commons geosearch: off" in (result.output_dir / "report.md").read_text("utf-8")
    assert "Commons geosearch: off" in result.summary()
    assert "places searched on Commons" in run(client, build(tmp_path / "on")).summary()


# --- time budget (world-safe nearby) ------------------------------------------------------------

JAIPUR_SEARCH = "26.920000|75.820000"


def _budget(chunk=1, minutes=60):
    return replace(geo_on(), chunk_cities=chunk, budget_minutes=minutes)


def _manifest(result):
    return json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))


@respx.mock
def test_past_the_budget_no_new_city_is_started_and_outputs_are_complete(client, tmp_path):
    wd = []
    result = run(
        client, build(tmp_path, world=True), wd, rules=_budget(), clock=clock_by_requests(wd)
    )
    # Jaipur misses 2 photographed places, Springfield 3: Jaipur goes first, then time is up.
    assert _manifest(result)["cities_not_reached"] == [3]
    places = rows(result.output_dir / "nearby_places.jsonl")
    assert places and {p["owner_page_id"] for p in places} == {1}
    verify_nearby(result.output_dir, None)
    assert result.totals["cities_reached"] == 1 and result.totals["cities_not_reached"] == 1
    assert "skipped for time: 1" in (result.output_dir / "report.md").read_text("utf-8")
    assert "skipped for time: 1" in result.summary()


@respx.mock
def test_a_rerun_reaches_further_because_cached_answers_cost_no_time(client, tmp_path):
    folders = build(tmp_path, world=True)
    wd = []
    run(client, folders, wd, rules=_budget(), clock=clock_by_requests(wd))
    wd2 = []
    again = run(client, folders, wd2, rules=_budget(), clock=clock_by_requests(wd2))
    assert again.totals["cities_not_reached"] == 0
    assert _manifest(again)["cities_not_reached"] == []
    assert not [p for p in wd2 if p.get("gscoord") == JAIPUR_SEARCH]  # Jaipur: from the cache


@respx.mock
def test_a_budget_spent_before_the_first_city_reaches_none_and_still_writes_valid_outputs(
    client, tmp_path
):
    wd, now = [], 1_000_000.0
    result = run(
        client,
        build(tmp_path, world=True),
        wd,
        rules=_budget(),
        clock=lambda: now,
        started_at=now - 61 * 60,
    )
    assert result.totals["cities_reached"] == 0 and result.totals["cities_not_reached"] == 2
    assert _manifest(result)["cities_not_reached"] == [1, 3]
    assert rows(result.output_dir / "nearby_places.jsonl") == []
    assert rows(result.output_dir / "nearby_candidates.jsonl") == []
    verify_nearby(result.output_dir, None)
    assert wd == []


@respx.mock
def test_cities_are_processed_in_priority_order(client, tmp_path):
    # Jaipur has no summary, so it cannot become ready this run: Springfield (a higher page id)
    # goes first although it misses more places.
    wd = []
    folders = build(tmp_path, world=True, summaries=(2, 3))
    result = run(client, folders, wd, rules=_budget(), clock=clock_by_requests(wd))
    assert _manifest(result)["cities_not_reached"] == [1]
