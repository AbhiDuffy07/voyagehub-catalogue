import csv
import json
from datetime import date

import pytest

from catalogue.enrich.timezones import (
    ATTRIBUTION,
    lookup_zones,
    run_derive_timezone,
    same_offsets,
)
from catalogue.errors import PipelineError
from tests.enrichfixture import destination_row, write_enrich_output

TODAY = date(2026, 11, 12)
JAIPUR, BERLIN, URUMQI, SEA = (26.92, 75.82), (52.52, 13.40), (43.80, 87.60), (0.0, -30.0)
BORDER, NOWHERE, EMPTY = (39.47, 75.99), (10.0, 10.0), (-10.0, -10.0)

ZONES = {
    JAIPUR: ("Asia/Kolkata",),
    BERLIN: ("Europe/Berlin",),
    URUMQI: ("Asia/Urumqi",),
    SEA: ("Etc/GMT+2",),
    BORDER: ("Asia/Shanghai", "Asia/Urumqi"),
    NOWHERE: ("Nowhere/Land",),
    EMPTY: (),
}


def locate(lat, lon):
    return ZONES[(lat, lon)]


def row(
    page_id,
    title,
    point,
    *,
    timezone=None,
    basis=None,
    role="destination",
    iso="IN",
    source="wikivoyage",
):
    base = destination_row(page_id, title, iso=iso, role=role)
    return {
        **base,
        "timezone": timezone,
        "timezone_basis": basis,
        "coord_lat": point[0] if point else None,
        "coord_long": point[1] if point else None,
        "coord_source": source if point else None,
    }


DESTINATIONS = [
    row(1, "Jaipur", JAIPUR, timezone="Asia/Kolkata", basis="wikidata_item"),
    row(2, "Berlin", BERLIN, iso="DE"),
    row(3, "Kashgar", URUMQI, timezone="Asia/Shanghai", basis="country_single_zone", iso="CN"),
    row(4, "Nowhere Land", None),
    row(5, "Atlantic", SEA, iso="BR"),
    row(6, "Border town", BORDER, iso="CN"),
    row(7, "Odd zone", NOWHERE),
    row(8, "Empty answer", EMPTY),
    row(9, "India", JAIPUR, role="browse", timezone="Asia/Kolkata", basis="wikidata_item"),
    row(10, "Outline", BERLIN, role="destination_outline", iso="DE", source="wikidata"),
    row(11, "Kolkata-ish", JAIPUR, timezone="Asia/Dhaka", basis="wikidata_item"),
]


@pytest.fixture
def enrich_folder(tmp_path):
    return write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=DESTINATIONS,
        listings=[],
        country_filter=None,
    )


def run(folder, tmp_path, **kw):
    return run_derive_timezone(
        enrich_folder=folder,
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        country_iso=None,
        locate=locate,
        data_version="2026e",
        **kw,
    )


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def table(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def result(enrich_folder, tmp_path):
    return run(enrich_folder, tmp_path)


def rows(result):
    return {
        r["wikivoyage_page_id"]: r for r in jsonl(result.output_dir / "destination_timezones.jsonl")
    }


def test_only_destinations_and_outline_destinations_get_a_row(result):
    assert set(rows(result)) == {1, 2, 3, 4, 5, 6, 7, 8, 10, 11}


def test_an_existing_wikidata_zone_is_kept_and_not_relabelled(result):
    row_ = rows(result)[1]
    assert (row_["timezone"], row_["timezone_basis"]) == ("Asia/Kolkata", "wikidata_item")
    assert row_["coordinate_timezone"] == "Asia/Kolkata" and row_["flags"] == []
    assert row_["timezone_attribution"] is None


def test_a_missing_zone_is_derived_from_the_coordinates_and_credited(result):
    row_ = rows(result)[2]
    assert (row_["timezone"], row_["timezone_basis"]) == ("Europe/Berlin", "coordinates")
    assert row_["timezone_boundary_data_version"] == "2026e"
    assert row_["timezone_attribution"] == ATTRIBUTION
    assert (
        ATTRIBUTION
        == "Time zones: timezone-boundary-builder, \u00a9 OpenStreetMap contributors, ODbL"
    )
    assert row_["flags"] == [] and row_["wikidata_timezone"] is None


def test_a_countrys_single_zone_is_replaced_by_the_coordinates_and_the_row_says_so(result):
    row_ = rows(result)[3]
    assert (row_["timezone"], row_["timezone_basis"]) == ("Asia/Urumqi", "coordinates")
    assert (
        row_["wikidata_timezone"] == "Asia/Shanghai"
        and row_["coordinate_timezone"] == "Asia/Urumqi"
    )
    assert row_["flags"] == ["timezone_replaced_country_zone"]
    assert row_["timezone_attribution"] == ATTRIBUTION


def test_a_places_own_wikidata_zone_is_never_replaced_only_flagged(result):
    row_ = rows(result)[11]
    assert (row_["timezone"], row_["timezone_basis"]) == ("Asia/Dhaka", "wikidata_item")
    assert row_["coordinate_timezone"] == "Asia/Kolkata" and row_["flags"] == ["timezone_disagrees"]
    assert row_["timezone_attribution"] is None


def test_a_different_name_for_the_same_offsets_is_not_a_disagreement(tmp_path):
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=[row(1, "Old name", JAIPUR, timezone="Asia/Calcutta", basis="wikidata_item")],
        listings=[],
        country_filter=None,
    )
    got = jsonl(run(folder, tmp_path).output_dir / "destination_timezones.jsonl")[0]
    assert got["timezone"] == "Asia/Calcutta" and got["flags"] == []


def test_a_countrys_zone_with_the_same_offsets_gets_the_exact_name_flagged_as_an_alias(tmp_path):
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=[
            row(1, "Alias", JAIPUR, timezone="Asia/Calcutta", basis="country_single_zone")
        ],
        listings=[],
        country_filter=None,
    )
    result_ = run(folder, tmp_path)
    got = jsonl(result_.output_dir / "destination_timezones.jsonl")[0]
    assert got["timezone"] == "Asia/Kolkata" and got["timezone_basis"] == "coordinates"
    assert got["flags"] == ["timezone_alias_normalised"]
    assert result_.totals["alias_normalised"] == 1 and result_.totals["replaced_country_zone"] == 0


def test_a_countrys_zone_stays_when_the_coordinates_give_no_single_zone(tmp_path):
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=[
            row(1, "Edge", BORDER, timezone="Asia/Shanghai", basis="country_single_zone")
        ],
        listings=[],
        country_filter=None,
    )
    got = jsonl(run(folder, tmp_path).output_dir / "destination_timezones.jsonl")[0]
    assert (got["timezone"], got["timezone_basis"], got["flags"]) == (
        "Asia/Shanghai",
        "country_single_zone",
        [],
    )


def test_a_destination_with_no_coordinate_and_no_zone_stays_empty_and_flagged(result):
    row_ = rows(result)[4]
    assert row_["timezone"] is None and row_["timezone_basis"] is None
    assert row_["flags"] == ["timezone_unknown"]


def test_a_point_at_sea_gets_no_zone(result):
    row_ = rows(result)[5]
    assert row_["timezone"] is None and row_["flags"] == ["timezone_no_land_zone"]


def test_an_empty_answer_is_the_same_as_no_land_zone(result):
    assert rows(result)[8]["flags"] == ["timezone_no_land_zone"]


def test_two_overlapping_zones_are_never_resolved_by_picking_one(result):
    row_ = rows(result)[6]
    assert row_["timezone"] is None and row_["flags"] == ["timezone_ambiguous"]


def test_a_name_the_time_zone_database_does_not_know_is_refused(result):
    row_ = rows(result)[7]
    assert row_["timezone"] is None and row_["flags"] == ["timezone_invalid_name"]


def test_a_filled_coordinate_is_used_like_an_own_one(result):
    assert rows(result)[10]["timezone"] == "Europe/Berlin"


def test_a_second_disagreement_is_flagged_too(result):
    row_ = rows(result)[11]
    assert row_["timezone"] == "Asia/Dhaka" and row_["flags"] == ["timezone_disagrees"]


def test_counts_by_country(result):
    by = {r["country_iso"]: r for r in table(result.output_dir / "timezones_by_country.csv")}
    assert by["DE"]["destinations"] == "2" and by["DE"]["from_coordinates"] == "2"
    assert by["CN"]["replaced_country_zone"] == "1" and by["CN"]["from_coordinates"] == "1"
    assert by["CN"]["still_none"] == "1" and by["CN"]["disagreements"] == "0"
    assert by["IN"]["from_wikidata_item"] == "2" and by["IN"]["disagreements"] == "1"


def test_disagreements_with_a_places_own_zone_are_listed_for_review(result):
    listed = table(result.output_dir / "timezone_disagreements.csv")
    assert [(r["page_id"], r["wikidata_timezone"], r["coordinate_timezone"]) for r in listed] == [
        ("11", "Asia/Dhaka", "Asia/Kolkata")
    ]


def test_manifest_records_inputs_data_version_and_output_hashes(result):
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["boundary_data_version"] == "2026e" and manifest["dump_date"] == "20261101"
    assert len(manifest["enrich_manifest_sha256"]) == 64
    assert "destination_timezones.jsonl" in manifest["outputs_sha256"]
    totals = manifest["totals"]
    assert totals["destinations"] == 10 and totals["from_coordinates"] == 3
    assert totals["from_country_single_zone"] == 0 and totals["replaced_country_zone"] == 1
    assert totals["still_none"] == 5 and totals["disagreements"] == 1


def test_the_summary_has_counts_only(result):
    text = result.summary()
    assert "10" in text and "Kashgar" not in text


def test_a_second_run_gives_identical_files(enrich_folder, tmp_path, result):
    first = (result.output_dir / "manifest.json").read_text(encoding="utf-8")
    again = run(enrich_folder, tmp_path)
    assert (again.output_dir / "manifest.json").read_text(encoding="utf-8") == first


def test_the_output_names_only_this_runs_files(result):
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["outputs_sha256"]) == {
        "destination_timezones.jsonl",
        "report.md",
        "timezone_disagreements.csv",
        "timezones_by_country.csv",
    }


# --- input checks -------------------------------------------------------------------------------


def test_a_changed_enrich_file_is_refused(enrich_folder, tmp_path):
    path = enrich_folder / "destination_facts.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="destination_facts.jsonl"):
        run(enrich_folder, tmp_path)


def test_a_missing_or_damaged_manifest_is_a_clear_error(enrich_folder, tmp_path):
    (enrich_folder / "manifest.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(PipelineError, match="manifest"):
        run(enrich_folder, tmp_path)
    (enrich_folder / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="manifest"):
        run(enrich_folder, tmp_path)


def test_an_enrich_output_for_another_country_filter_is_refused(enrich_folder, tmp_path):
    with pytest.raises(PipelineError, match="country"):
        run_derive_timezone(
            enrich_folder=enrich_folder,
            out_dir=tmp_path / "out",
            dump_date="20261101",
            today=TODAY,
            country_iso="IN",
            locate=locate,
            data_version="2026e",
        )


def test_a_bad_dump_date_is_a_clear_error(enrich_folder, tmp_path):
    with pytest.raises(PipelineError, match="YYYYMMDD"):
        run_derive_timezone(
            enrich_folder=enrich_folder,
            out_dir=tmp_path / "out",
            dump_date="2026-11-01",
            today=TODAY,
            country_iso=None,
            locate=locate,
            data_version="2026e",
        )


def test_a_damaged_row_is_a_clear_error_without_quoting_it(tmp_path):
    import hashlib

    folder = write_enrich_output(
        tmp_path / "bad", destinations=[], listings=[], country_filter=None
    )
    path = folder / "destination_facts.jsonl"
    path.write_text('{"secret": 1}\n', encoding="utf-8")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["destination_facts.jsonl"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError) as raised:
        run(folder, tmp_path)
    assert "secret" not in str(raised.value)


# --- the real package ---------------------------------------------------------------------------


def test_the_real_lookup_takes_latitude_first_and_knows_land_and_sea():
    # tzfpy itself wants (longitude, latitude); a swapped call silently gives Etc/GMT-2 for Jaipur.
    assert lookup_zones(26.92, 75.82) == ("Asia/Kolkata",)
    assert lookup_zones(52.52, 13.405) == ("Europe/Berlin",)
    assert lookup_zones(40.71, -74.0) == ("America/New_York",)
    assert all(zone.startswith("Etc/") for zone in lookup_zones(0.0, -30.0))


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (True, 10.0),
        ("26.9", "75.8"),
        (float("nan"), 75.8),
        (float("inf"), 75.8),
        (91.0, 75.8),
        (26.9, 181.0),
    ],
)
def test_bad_coordinates_are_flagged_as_bad_data_and_never_echoed(tmp_path, lat, lon):
    odd = {**row(1, "Odd", None), "coord_lat": lat, "coord_long": lon}
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101", destinations=[odd], listings=[], country_filter=None
    )
    result_ = run(folder, tmp_path)
    got = jsonl(result_.output_dir / "destination_timezones.jsonl")[0]
    assert got["timezone"] is None and got["flags"] == ["timezone_bad_coordinates"]
    assert got["coord_lat"] is None and got["coord_long"] is None
    text_ = (result_.output_dir / "destination_timezones.jsonl").read_text(encoding="utf-8")
    assert "NaN" not in text_ and "Infinity" not in text_


def test_one_coordinate_alone_is_no_point(tmp_path):
    half = {**row(1, "Half", None), "coord_lat": 26.9, "coord_long": None}
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101", destinations=[half], listings=[], country_filter=None
    )
    got = jsonl(run(folder, tmp_path).output_dir / "destination_timezones.jsonl")[0]
    assert got["flags"] == ["timezone_unknown"]


@pytest.mark.parametrize("point", [SEA, NOWHERE, EMPTY])
def test_a_countrys_zone_stays_when_the_coordinates_give_sea_or_an_unknown_name(tmp_path, point):
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=[row(1, "Kept", point, timezone="Asia/Kolkata", basis="country_single_zone")],
        listings=[],
        country_filter=None,
    )
    got = jsonl(run(folder, tmp_path).output_dir / "destination_timezones.jsonl")[0]
    assert (got["timezone"], got["timezone_basis"], got["flags"]) == (
        "Asia/Kolkata",
        "country_single_zone",
        [],
    )


def test_a_wikidata_zone_name_the_database_does_not_know_is_not_trusted(tmp_path):
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=[row(1, "Odd name", JAIPUR, timezone="Mars/Olympus", basis="wikidata_item")],
        listings=[],
        country_filter=None,
    )
    got = jsonl(run(folder, tmp_path).output_dir / "destination_timezones.jsonl")[0]
    assert (got["timezone"], got["timezone_basis"]) == ("Asia/Kolkata", "coordinates")
    assert got["flags"] == ["timezone_invalid_wikidata_name"]
    assert got["wikidata_timezone"] == "Mars/Olympus"


def test_same_offsets_never_raises_on_odd_names():
    assert same_offsets("Asia/Calcutta", "Asia/Kolkata") is True
    assert same_offsets("Asia/Dhaka", "Asia/Kolkata") is False
    assert same_offsets(123, "Asia/Kolkata") is False  # type: ignore[arg-type]
    assert same_offsets("Mars/Olympus", "Asia/Kolkata") is False
    assert same_offsets("", "Asia/Kolkata") is False


def test_every_disagreement_is_listed_not_just_a_sample(tmp_path):
    many = [
        row(n, f"Place {n}", JAIPUR, timezone="Asia/Dhaka", basis="wikidata_item")
        for n in range(1, 601)
    ]
    folder = write_enrich_output(
        tmp_path / "enrich" / "20261101", destinations=many, listings=[], country_filter=None
    )
    done = run(folder, tmp_path)
    assert len(table(done.output_dir / "timezone_disagreements.csv")) == 600
