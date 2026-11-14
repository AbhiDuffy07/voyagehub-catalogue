import csv
import json
from datetime import date

import pytest

from catalogue.blocks.check_run import run_auto_check
from catalogue.errors import PipelineError
from tests.blocksfixture import sha
from tests.checkfixture import make_blocks

TODAY = date(2026, 11, 14)

CITIES = {
    # three sights, then a stadium: one block of four, flagged
    "jaipur": [
        ("Amer Fort", "religious"),
        ("Panna Meena", "sight"),
        ("Jal Mahal", "sight"),
        ("Sawai Mansingh Stadium", None),
    ],
    # a sight and a cinema: rejected
    "agra": [("Agra Fort", "fort_palace"), ("Sapna Cinema", None)],
    # three clean stops: checked
    "varanasi": [
        ("Assi Ghat", "sight"),
        ("Durga Mandir", "religious"),
        ("Tulsi Ghat", "sight"),
    ],
}


@pytest.fixture
def blocks(tmp_path):
    return make_blocks(tmp_path, CITIES)


def check(blocks, tmp_path, **kw):
    return run_auto_check(
        blocks_folder=blocks,
        out_dir=tmp_path / "checks",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        **kw,
    )


def rows(result):
    path = result.output_dir / "block_checks.jsonl"
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()]


def test_every_block_gets_exactly_one_status_and_reasons(blocks, tmp_path):
    result = check(blocks, tmp_path)
    produced = [
        json.loads(x)["block_id"]
        for x in (blocks / "day_blocks.jsonl").read_text("utf-8").splitlines()
    ]
    checked = rows(result)
    assert sorted(r["block_id"] for r in checked) == sorted(produced)
    by_city = {r["owner_slug"]: r for r in checked}
    assert by_city["jaipur"]["check_status"] == "flagged"
    assert by_city["jaipur"]["check_failures"] == ["non_sight_stop"]
    assert by_city["jaipur"]["flagged_stops"][0]["word"] == "stadium"
    assert by_city["agra"]["check_status"] == "rejected"
    assert by_city["agra"]["check_failures"] == ["non_sight_stops"]
    assert by_city["varanasi"]["check_status"] == "checked"
    assert by_city["varanasi"]["check_failures"] == []
    assert all(r["check_rules_version"] == 1 for r in checked)
    assert result.totals["by_status"] == {"checked": 1, "flagged": 1, "rejected": 1}


def test_nothing_is_dropped_and_the_blocks_files_are_untouched(blocks, tmp_path):
    before = {n: (blocks / n).read_bytes() for n in ("day_blocks.jsonl", "day_block_stops.jsonl")}
    result = check(blocks, tmp_path)
    assert {n: (blocks / n).read_bytes() for n in before} == before
    flagged = [r for r in rows(result) if r["flagged_stops"]]
    assert len(flagged) == 2  # the flagged and the rejected block both name their stops


def test_report_has_per_city_status_counts_days_and_matched_words(blocks, tmp_path):
    text = (check(blocks, tmp_path).output_dir / "report.md").read_text("utf-8")
    assert "Jaipur" in text and "Agra" in text and "Varanasi" in text
    assert "checked" in text and "flagged" in text and "rejected" in text
    assert "days" in text.lower() and "`stadium`" in text and "`cinema`" in text
    summary = list(
        csv.DictReader(
            (check(blocks, tmp_path).output_dir / "city_summary.csv").open(encoding="utf-8")
        )
    )
    jaipur = next(r for r in summary if r["owner_slug"] == "jaipur")
    assert (jaipur["blocks"], jaipur["checked"], jaipur["flagged"], jaipur["rejected"]) == (
        "1",
        "0",
        "1",
        "0",
    )
    assert {"days_fillable_checked", "days_fillable_all"} <= set(summary[0])


def test_days_from_checked_blocks_never_exceed_the_days_from_all_blocks(blocks, tmp_path):
    t = check(blocks, tmp_path).totals
    assert t["days_fillable_checked"] <= t["days_fillable_all"]


def test_flagged_sample_lists_flagged_and_rejected_blocks(blocks, tmp_path):
    text = (check(blocks, tmp_path).output_dir / "flagged_sample.md").read_text("utf-8")
    assert "Sawai Mansingh Stadium" in text and "Sapna Cinema" in text
    assert "Assi Ghat" not in text  # a checked block is not in the sample


def test_rerun_is_byte_identical_and_manifest_hashes_match(blocks, tmp_path):
    first = check(blocks, tmp_path)
    names = ("block_checks.jsonl", "city_summary.csv", "flagged_sample.md", "report.md")
    before = {n: (first.output_dir / n).read_bytes() for n in names}
    second = check(blocks, tmp_path)
    assert {n: (second.output_dir / n).read_bytes() for n in names} == before
    manifest = json.loads((second.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["check_rules_version"] == 1 and manifest["country_filter"] == "IN"
    assert set(manifest["outputs_sha256"]) == set(names)
    for name, digest in manifest["outputs_sha256"].items():
        assert sha(second.output_dir / name) == digest


def test_a_changed_blocks_file_is_refused(blocks, tmp_path):
    (blocks / "day_blocks.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="day_blocks.jsonl does not match"):
        check(blocks, tmp_path)


def test_a_different_country_filter_is_refused(blocks, tmp_path):
    with pytest.raises(PipelineError, match="country"):
        run_auto_check(
            blocks_folder=blocks,
            out_dir=tmp_path / "checks",
            dump_date="20261101",
            today=TODAY,
            country_iso="FR",
        )


def test_a_missing_manifest_names_the_job_to_run(blocks, tmp_path):
    (blocks / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="run draft-blocks first"):
        check(blocks, tmp_path)


def test_a_stop_in_two_blocks_is_rejected_from_the_files(blocks, tmp_path):
    lines = (blocks / "day_block_stops.jsonl").read_text("utf-8").splitlines()
    rows_ = [json.loads(x) for x in lines]
    other = next(r for r in rows_ if r["block_id"].startswith("agra"))
    mine = next(r for r in rows_ if r["block_id"].startswith("varanasi"))
    mine.update({k: other[k] for k in ("listing_key", "page_id", "ordinal", "source")})
    (blocks / "day_block_stops.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows_), encoding="utf-8"
    )
    manifest = json.loads((blocks / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["day_block_stops.jsonl"] = sha(blocks / "day_block_stops.jsonl")
    (blocks / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = check(blocks, tmp_path)
    reused = [r for r in rows(result) if "stop_used_twice" in r["check_failures"]]
    assert {r["owner_slug"] for r in reused} == {"agra", "varanasi"}


def _rewrite(blocks, name, mutate):
    path = blocks / name
    rows_ = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    for row in rows_:
        mutate(row)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows_), encoding="utf-8")
    manifest = json.loads((blocks / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"][name] = sha(path)
    (blocks / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("day_block_stops.jsonl", lambda r: r.pop("source")),
        ("day_block_stops.jsonl", lambda r: r.update(page_id=[1])),
        ("day_block_stops.jsonl", lambda r: r.update(hop_minutes="far")),
        ("day_block_stops.jsonl", lambda r: r.update(name=5)),
        ("day_blocks.jsonl", lambda r: r.pop("title")),
        ("day_blocks.jsonl", lambda r: r.update(owner_slug=["x"])),
        ("day_blocks.jsonl", lambda r: r.update(minutes="long")),
    ],
)
def test_a_hash_valid_but_damaged_row_is_one_clear_error(blocks, tmp_path, name, mutate):
    _rewrite(blocks, name, mutate)
    with pytest.raises(PipelineError, match="has a damaged row"):
        check(blocks, tmp_path)


def test_blocks_built_under_other_rules_are_refused(blocks, tmp_path):
    manifest = json.loads((blocks / "manifest.json").read_text(encoding="utf-8"))
    manifest["rules_version"] = 1
    (blocks / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="run draft-blocks again"):
        check(blocks, tmp_path)


def test_the_sample_cannot_break_its_own_structure(blocks, tmp_path):
    _rewrite(blocks, "day_block_stops.jsonl", lambda r: r.update(name=r["name"] + "\n# Injected"))
    text = (check(blocks, tmp_path).output_dir / "flagged_sample.md").read_text("utf-8")
    assert "\n# Injected" not in text
