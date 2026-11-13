import csv
import json
from datetime import date

import pytest

from catalogue.errors import PipelineError
from catalogue.photos.select import load_selection_rules
from catalogue.photos.select_run import run_photo_select
from tests.photoselectfixture import candidate_row as cr
from tests.photoselectfixture import dest, listing, sha, write_b5

TODAY = date(2026, 11, 13)
RULES = load_selection_rules()
FEATURED = "Featured pictures on Wikimedia Commons"

DESTINATIONS = [dest(1), dest(2), dest(3), dest(4), dest(5), dest(6, iso="FR")]
LISTINGS = [
    listing(1, 1, "see:amber"),
    listing(1, 2, "do:walk", kind="do"),
    listing(1, 3, "eat:cafe", kind="eat"),
]
CANDIDATES = [
    cr(1, "Jaipur.jpg"),  # P18, fine
    cr(1, "Jaipur F.jpg", sources=("commons_category",), marks=[FEATURED]),  # lower tier than P18
    cr(2, "Agra tall.jpg", width=1400, height=2100),  # portrait: passes with a flag
    cr(3, "Delhi map.jpg"),  # dropped by name
    cr(4, "Pune small.jpg", width=900, height=600),  # below the gate
    cr(5, "Goa.jpg", usable=False, flags=["licence_not_allowed"]),
    cr(
        1,
        "Amer.jpg",
        ordinal=1,
        subject="listing",
        listing_key="see:amber",
        width=2400,
        height=1600,
    ),
    cr(
        1,
        "Walk.jpg",
        ordinal=2,
        subject="listing",
        listing_key="do:walk",
        sources=("commons_category",),
        marks=["Quality images"],
        width=1100,
        height=800,
    ),
    cr(6, "Paris.jpg", country="FR", width=3000, height=2000),
]
COVERAGE = {
    "all": {},
    "by_country": {
        "IN": {
            "articles": {"total": 5, "with_usable": 3},
            "listings": {"total": 9, "with_usable": 2},
        },
        "FR": {"articles": {"total": 1, "with_usable": 1}},
    },
}


@pytest.fixture
def folders(tmp_path):
    return write_b5(
        tmp_path,
        destinations=DESTINATIONS,
        listings=LISTINGS,
        candidates=CANDIDATES,
        coverage=COVERAGE,
        country_filter=None,
    )


def run(folders, tmp_path, **kw):
    photos, enrich = folders
    return run_photo_select(
        photos_folder=photos,
        enrich_folder=enrich,
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        rules=kw.pop("rules", RULES),
        rejected=kw.pop("rejected", frozenset()),
        **kw,
    )


def rows_of(result):
    path = result.output_dir / "photo_selection.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def by_subject(rows):
    return {(r["page_id"], r["ordinal"]): r for r in rows}


def test_one_row_per_subject_with_the_choice_or_the_reason(folders, tmp_path):
    rows = by_subject(rows_of(run(folders, tmp_path)))
    assert len(rows) == 8  # 6 articles + 2 see/do listings (the eat listing is not a subject)
    jaipur = rows[(1, 0)]
    assert jaipur["photo"]["commons_file"] == "Jaipur.jpg" and jaipur["tier"] == "p18"
    assert jaipur["approved_by"] == f"auto:{RULES.version}" and jaipur["hero_quality"] is True
    assert jaipur["alternatives"] == ["Jaipur F.jpg"] and jaipur["candidates"] == 2
    assert jaipur["photo"]["author"] == "Ann" and jaipur["photo"]["licence_code"] == "cc-by-sa-4.0"
    assert jaipur["photo"]["attribution"].endswith("via Wikimedia Commons")

    agra = rows[(2, 0)]
    assert agra["photo"]["commons_file"] == "Agra tall.jpg" and agra["portrait"] is True
    assert "portrait" in agra["flags"]

    for key, flag in [
        ((3, 0), "only_excluded"),
        ((4, 0), "only_below_gate"),
        ((5, 0), "no_usable_candidate"),
    ]:
        assert rows[key]["photo"] is None and rows[key]["flags"] == [flag]
        assert rows[key]["approved_by"] is None and rows[key]["tier"] is None

    assert rows[(1, 1)]["listing_key"] == "see:amber"
    assert rows[(1, 2)]["tier"] == "quality"


def test_subjects_with_no_candidate_row_are_flagged_not_dropped(folders, tmp_path):
    rows = by_subject(rows_of(run(folders, tmp_path)))
    assert rows[(6, 0)]["photo"] is not None
    # Remove Paris from the candidates: it must still appear, flagged.
    photos, enrich = folders
    path = photos / "photo_candidates.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if "Paris" not in line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    manifest = json.loads((photos / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["photo_candidates.jsonl"] = sha(path)
    (photos / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    again = by_subject(rows_of(run(folders, tmp_path)))
    assert again[(6, 0)]["photo"] is None and again[(6, 0)]["flags"] == ["no_candidate"]


def test_coverage_uses_b5s_denominators_and_counts_the_selected(folders, tmp_path):
    result = run(folders, tmp_path)
    coverage = result.totals["coverage"]["by_country"]
    assert coverage["IN"]["articles"] == {"total": 5, "selected": 2}
    assert coverage["IN"]["listings"] == {"total": 9, "selected": 2}
    assert coverage["FR"]["articles"] == {"total": 1, "selected": 1}
    report = (result.output_dir / "report.md").read_text("utf-8")
    assert "Destinations: 3 of 6" in report and "See/do listings: 2 of 9" in report


def test_gate_cost_is_reported_with_counts(folders, tmp_path):
    result = run(folders, tmp_path)
    gate = result.totals["gate"]
    # Covered (survived the drops): articles 1, 2, 4, 6 and 2 listings = 6 subjects; page 4
    # is the one the gate removes.
    assert gate == {"covered": 6, "lost": 1, "percent": 16.7, "over_limit": True}
    lost = list(csv.DictReader((result.output_dir / "lost_to_gate.csv").open(encoding="utf-8")))
    assert [(r["page_id"], r["best_long_side"]) for r in lost] == [("4", "900")]
    report = (result.output_dir / "report.md").read_text("utf-8")
    assert report.splitlines()[2].startswith("**WARNING")
    assert "not frozen" in report


def test_no_warning_when_the_gate_costs_under_five_percent(tmp_path):
    destinations = [dest(n) for n in range(1, 41)]
    candidates = [cr(n, f"P{n}.jpg") for n in range(1, 41)]
    folders = write_b5(
        tmp_path,
        destinations=destinations,
        listings=[],
        candidates=candidates,
        country_filter=None,
    )
    result = run(folders, tmp_path)
    assert result.totals["gate"] == {"covered": 40, "lost": 0, "percent": 0.0, "over_limit": False}
    assert "WARNING" not in (result.output_dir / "report.md").read_text("utf-8")


def test_tier_portrait_and_hero_counts(folders, tmp_path):
    totals = run(folders, tmp_path).totals
    assert totals["tiers"] == {"p18": 4, "quality": 1}
    assert totals["portrait"] == 1
    assert totals["hero_quality"] == 4  # Jaipur, Agra, Amer, Paris (Walk is only 1,100 px)


def test_an_override_file_is_never_chosen(folders, tmp_path):
    result = run(folders, tmp_path, rejected=frozenset({"Jaipur.jpg"}))
    jaipur = by_subject(rows_of(result))[(1, 0)]
    assert jaipur["photo"]["commons_file"] == "Jaipur F.jpg" and jaipur["tier"] == "featured"


def test_spot_check_sample_is_seeded_and_deterministic(tmp_path):
    destinations = [dest(n) for n in range(1, 61)]
    candidates = [
        cr(n, f"P{n}.jpg", width=1100 + 20 * n, height=800)
        if n % 3
        else cr(n, f"Q{n}.jpg", sources=("commons_category",), marks=["Quality images"])
        for n in range(1, 61)
    ]
    folders = write_b5(
        tmp_path,
        destinations=destinations,
        listings=[],
        candidates=candidates,
        country_filter=None,
    )
    first = run(folders, tmp_path)
    sample = (first.output_dir / "spot_check_sample.csv").read_bytes()
    rows = list(csv.DictReader((first.output_dir / "spot_check_sample.csv").open(encoding="utf-8")))
    assert 25 <= len(rows) <= 30
    assert len({(r["page_id"], r["ordinal"]) for r in rows}) == len(rows)  # no repeats
    assert {r["bucket"] for r in rows} == {"p18", "other_tier", "lowest_score", "narrow"}
    second = run(folders, tmp_path)
    assert (second.output_dir / "spot_check_sample.csv").read_bytes() == sample


def test_outputs_manifest_and_determinism(folders, tmp_path):
    first = run(folders, tmp_path)
    folder = first.output_dir
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection_rules_version"] == RULES.version
    assert manifest["country_filter"] is None
    assert set(manifest["outputs_sha256"]) == {
        "flags_by_country.csv",
        "lost_to_gate.csv",
        "photo_selection.jsonl",
        "report.md",
        "selection_by_country.csv",
        "spot_check_sample.csv",
    }
    for name, digest in manifest["outputs_sha256"].items():
        assert sha(folder / name) == digest
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    second = run(folders, tmp_path)
    assert {p.name: p.read_bytes() for p in second.output_dir.iterdir()} == before


def test_summary_is_counts_only(folders, tmp_path):
    summary = run(folders, tmp_path).summary()
    assert "selected: 5 of 8" in summary and "size gate" in summary
    assert "Jaipur" not in summary


# --- refusals -----------------------------------------


def test_an_edited_candidates_file_is_refused(folders, tmp_path):
    photos, _ = folders
    with (photos / "photo_candidates.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(PipelineError, match="does not match the photo-candidates manifest"):
        run(folders, tmp_path)


def test_a_different_country_filter_is_refused(folders, tmp_path):
    with pytest.raises(PipelineError, match="different country filter"):
        run(folders, tmp_path, country_iso="IN")


def test_enrich_facts_that_are_not_the_ones_b5_read_are_refused(folders, tmp_path):
    photos, enrich = folders
    manifest = json.loads((photos / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs_sha256"]["destination_facts.jsonl"] = "0" * 64
    (photos / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="not the one photo-candidates read"):
        run(folders, tmp_path)


def test_a_candidate_for_an_unknown_subject_is_refused(tmp_path):
    folders = write_b5(
        tmp_path,
        destinations=[dest(1)],
        listings=[],
        candidates=[cr(99, "X.jpg")],
        country_filter=None,
    )
    with pytest.raises(PipelineError, match="not in the enrich output"):
        run(folders, tmp_path)


def test_missing_manifest_names_the_job_to_run(folders, tmp_path):
    photos, _ = folders
    (photos / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="run photo-candidates first"):
        run(folders, tmp_path)


def test_a_damaged_row_is_named_by_file_only(folders, tmp_path):
    photos, _ = folders
    path = photos / "photo_candidates.jsonl"
    path.write_text('{"page_id": 1}\n', encoding="utf-8")
    manifest = json.loads((photos / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["photo_candidates.jsonl"] = sha(path)
    (photos / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="photo_candidates.jsonl has a damaged row"):
        run(folders, tmp_path)
