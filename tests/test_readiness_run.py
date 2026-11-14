import csv
import json

import pytest

from catalogue.errors import PipelineError
from catalogue.readiness.run import _verify_timezones, run_readiness
from tests.readinessfixture import TODAY, build, rows, run, sha


@pytest.fixture
def world(tmp_path):
    return run(tmp_path)


def test_each_city_gets_its_tier_and_failed_rules(world):
    result, _ = world
    r = rows(result)
    assert r[1]["tier"] == "plannable" and r[1]["failed_rules"] == []
    assert r[2]["tier"] == "browse_only" and r[2]["days_fillable"] == 0
    assert r[3]["tier"] == "not_ready"
    assert r[3]["failed_rules"] == ["summary", "checked_block"]
    assert r[5]["tier"] == "not_ready"
    assert r[5]["failed_rules"] == ["timezone", "photographed_places", "checked_block"]


def test_only_cities_are_assessed_and_the_rest_is_counted(world):
    result, _ = world
    assert set(rows(result)) == {1, 2, 3, 5}
    assert result.totals["not_assessed_type"] == 2  # the region and the district


def test_a_districts_listings_count_for_the_owning_city_without_duplicates(world):
    result, _ = world
    agra = rows(result)[2]
    assert agra["photographed_places"] == 3  # the duplicate's photo is not counted
    assert agra["hero_photos"] == 1  # its first place is a hero photo, owned by Agra


def test_a_sleep_listing_photo_is_not_a_photographed_place(world):
    result, _ = world
    kochi = rows(result)[5]  # its sleep listing has a hero photo too, which counts for nothing
    assert kochi["photographed_places"] == 1 and kochi["hero_photos"] == 1
    assert result.totals["photos_of_non_places"] == 2  # the sleep photo and the duplicate's


def test_blocks_are_joined_by_page_id_even_when_the_slug_is_a_number(world):
    result, _ = world
    varanasi = rows(result)[3]
    assert varanasi["checked_blocks"] == 0 and varanasi["flagged_or_rejected_blocks"] == 2


def test_a_city_carries_its_slug_title_country_and_wikivoyage_status(world):
    result, _ = world
    jaipur = rows(result)[1]
    assert (jaipur["slug"], jaipur["title"], jaipur["country_iso"]) == ("jaipur", "Jaipur", "IN")
    assert rows(result)[3]["wikivoyage_status"] == "outline"


def test_counts_per_rule_and_per_country_are_written(world):
    result, _ = world
    by_rule = {r["rule"]: r for r in csv.DictReader((result.output_dir / "by_rule.csv").open())}
    assert by_rule["summary"]["failing"] == "1" and by_rule["timezone"]["failing"] == "1"
    assert by_rule["photographed_places"]["failing"] == "1"
    assert by_rule["checked_block"]["failing"] == "2"
    assert by_rule["checked_block"]["only_failing"] == "0"
    country = list(csv.DictReader((result.output_dir / "by_country.csv").open()))
    assert country == [
        {
            "country_iso": "IN",
            "assessed": "4",
            "ready": "2",
            "plannable": "1",
            "browse_only": "1",
            "not_ready": "2",
        }
    ]


def test_the_report_and_sample_name_every_tier(world):
    result, _ = world
    report = (result.output_dir / "report.md").read_text(encoding="utf-8")
    assert "readiness_rules_version 1" in report and "browse only" in report
    sample = (result.output_dir / "sample.md").read_text(encoding="utf-8")
    for title in ("Jaipur", "Agra", "Varanasi", "Kochi"):
        assert title in sample


def test_a_first_run_publishes_the_ready_cities(world):
    result, _ = world
    r = rows(result)
    assert (r[1]["publish_action"], r[1]["published"]) == ("publish", True)
    assert (r[3]["publish_action"], r[3]["published"]) == ("none", False)
    state = json.loads((result.output_dir / "state.json").read_text(encoding="utf-8"))
    assert state["cities"]["1"] == {"published": True, "failed_runs": 0}
    assert result.totals["publish"] == 2


def test_a_ready_city_with_no_slug_is_counted_and_not_published(tmp_path):
    kwargs = build(tmp_path)  # Agra is ready (browse only) but has no slug in the registry
    registry = json.loads(kwargs["slugs_path"].read_text(encoding="utf-8"))
    del registry["entries"]["2"]
    kwargs["slugs_path"].write_text(json.dumps(registry), encoding="utf-8")
    result = run_readiness(**kwargs)
    agra = rows(result)[2]
    assert (agra["tier"], agra["slug"]) == ("browse_only", None)  # still ready by the rules
    assert (agra["publish_action"], agra["published"]) == ("blocked_no_slug", False)
    assert result.totals["ready"] == 2 and result.totals["blocked_no_slug"] == 1
    assert result.totals["publish"] == 1
    assert "no slug" in (result.output_dir / "report.md").read_text(encoding="utf-8")
    state = json.loads((result.output_dir / "state.json").read_text(encoding="utf-8"))
    assert state["cities"]["2"] == {"published": False, "failed_runs": 0}


def test_a_published_city_is_unpublished_only_after_two_failed_runs(tmp_path):
    first, _ = run(tmp_path, tag="1")
    second, _ = run(
        tmp_path, tag="2", summaries=(2, 5), previous_folder=first.output_dir
    )  # Jaipur loses its summary
    assert rows(second)[1]["publish_action"] == "keep_pending_unpublish"
    assert rows(second)[1]["published"] is True
    third, _ = run(tmp_path, tag="3", summaries=(2, 5), previous_folder=second.output_dir)
    assert rows(third)[1]["publish_action"] == "unpublish"
    assert rows(third)[1]["published"] is False
    assert third.totals["unpublish"] == 1


def test_a_published_city_that_left_the_run_counts_as_a_failed_run(tmp_path):
    first, _ = run(tmp_path, tag="1")
    state_path = first.output_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cities"]["99"] = {"published": True, "failed_runs": 1}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second, _ = run(tmp_path, tag="2", previous_folder=first.output_dir)
    new = json.loads((second.output_dir / "state.json").read_text(encoding="utf-8"))
    assert new["cities"]["99"] == {"published": False, "failed_runs": 2}
    assert second.totals["missing_from_run"] == 1


def test_a_damaged_previous_state_is_one_clear_error(tmp_path):
    first, _ = run(tmp_path, tag="1")
    (first.output_dir / "state.json").write_text("{", encoding="utf-8")
    with pytest.raises(PipelineError, match="state.json"):
        run(tmp_path, tag="2", previous_folder=first.output_dir)


def test_a_previous_state_from_another_country_filter_is_refused(tmp_path):
    first, _ = run(tmp_path, tag="1")
    state_path = first.output_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["country_filter"] = "BT"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(PipelineError, match="country"):
        run(tmp_path, tag="2", previous_folder=first.output_dir)


def test_the_manifest_records_inputs_outputs_and_versions(world):
    result, _ = world
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["readiness_rules_version"] == 1 and manifest["country_filter"] == "IN"
    assert set(manifest["inputs_sha256"]) >= {
        "listings.jsonl",
        "summaries.jsonl",
        "photo_selection.jsonl",
        "day_blocks.jsonl",
        "block_checks.jsonl",
        "destination_timezones.jsonl",
        "articles.jsonl",
    }
    assert "city_readiness.jsonl" in manifest["outputs_sha256"]


def test_a_second_run_is_identical(tmp_path):
    a, kwargs = run(tmp_path, tag="1")
    b = run_readiness(**{**kwargs, "out_dir": tmp_path / "again"})
    for name in ("city_readiness.jsonl", "report.md", "sample.md", "state.json"):
        assert (a.output_dir / name).read_bytes() == (b.output_dir / name).read_bytes()


def test_an_edited_input_is_refused(world):
    result, kwargs = world
    path = kwargs["photos_folder"] / "photo_selection.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="does not match"):
        run_readiness(**kwargs)


def test_checks_built_from_other_blocks_rules_are_refused(tmp_path):
    with pytest.raises(PipelineError, match="run auto-check again"):
        run(tmp_path, blocks_rules=2, checks_rules=1)


def test_a_different_country_filter_is_refused(world):
    _, kwargs = world
    with pytest.raises(PipelineError, match="different country filter"):
        run_readiness(**{**kwargs, "country_iso": "BT"})


def test_a_damaged_row_is_one_clear_error_naming_the_file_only(tmp_path):
    kwargs = build(tmp_path)
    path = kwargs["checks_folder"] / "block_checks.jsonl"
    path.write_text(json.dumps({"block_id": "x"}) + "\n", encoding="utf-8")
    manifest_path = kwargs["checks_folder"] / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["outputs_sha256"]["block_checks.jsonl"] = sha(path)
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PipelineError, match="block_checks.jsonl"):
        run_readiness(**kwargs)


def test_the_run_date_is_the_generation_date(world):
    result, _ = world
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_on"] == TODAY.isoformat()


def test_checks_built_from_other_blocks_than_the_ones_read_are_refused(tmp_path):
    kwargs = build(tmp_path)
    path = kwargs["blocks_folder"] / "day_blocks.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    manifest_path = kwargs["blocks_folder"] / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["outputs_sha256"]["day_blocks.jsonl"] = sha(path)  # draft-blocks was run again
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PipelineError, match="run auto-check again"):
        run_readiness(**kwargs)


def test_an_input_from_another_dump_is_refused(tmp_path):
    kwargs = build(tmp_path)
    manifest_path = kwargs["summaries_folder"] / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["dump_date"] = "20260801"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PipelineError, match="wikipedia-summary.*another dump"):
        run_readiness(**kwargs)


def test_the_manifest_records_the_state_and_slug_inputs(tmp_path):
    first, _ = run(tmp_path, tag="1")
    second, _ = run(tmp_path, tag="2", previous_folder=first.output_dir)
    manifest = json.loads((second.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_sha256"]["previous_state.json"] == sha(first.output_dir / "state.json")
    assert len(manifest["inputs_sha256"]["slugs.json"]) == 64


def test_a_city_that_left_the_run_is_counted_in_the_unpublish_totals(tmp_path):
    first, _ = run(tmp_path, tag="1")
    state_path = first.output_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cities"]["99"] = {"published": True, "failed_runs": 1}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second, _ = run(tmp_path, tag="2", previous_folder=first.output_dir)
    assert second.totals["unpublish"] == 1 and second.totals["missing_from_run"] == 1
    assert "unpublished after 2 failed runs: 1" in (second.output_dir / "report.md").read_text(
        "utf-8"
    )


def test_a_published_city_that_lost_its_slug_stays_published_and_is_flagged(tmp_path):
    first, _ = run(tmp_path, tag="1")
    kwargs = build(tmp_path, tag="2")
    registry = json.loads(kwargs["slugs_path"].read_text(encoding="utf-8"))
    del registry["entries"]["1"]
    kwargs["slugs_path"].write_text(json.dumps(registry), encoding="utf-8")
    second = run_readiness(**{**kwargs, "previous_folder": first.output_dir})
    jaipur = rows(second)[1]
    assert (jaipur["publish_action"], jaipur["published"], jaipur["slug"]) == ("keep", True, None)
    assert second.totals["published_without_slug"] == 1
    assert "published without a slug: 1" in (second.output_dir / "report.md").read_text("utf-8")


@pytest.mark.parametrize(
    ("recorded", "wanted", "ok"),
    [(None, "IN", True), ("IN", "IN", True), ("BT", "IN", False), ("IN", None, False)],
)
def test_the_time_zone_folder_may_be_the_world_run_but_never_another_country(
    tmp_path, recorded, wanted, ok
):
    kwargs = build(tmp_path)
    folder = kwargs["timezones_folder"]
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    data["country_filter"] = recorded
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    if ok:
        assert len(_verify_timezones(folder, wanted)) == 64
    else:
        with pytest.raises(PipelineError, match="different country filter"):
            _verify_timezones(folder, wanted)


@pytest.mark.parametrize(
    "bad",
    [
        {"published": "false", "failed_runs": 0},
        {"published": True, "failed_runs": -1},
        {"published": True, "failed_runs": "1"},
    ],
)
def test_a_state_entry_with_the_wrong_types_is_refused(tmp_path, bad):
    first, _ = run(tmp_path, tag="1")
    state_path = first.output_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cities"]["1"] = bad
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(PipelineError, match="state.json"):
        run(tmp_path, tag="2", previous_folder=first.output_dir)


def test_a_previous_state_with_another_schema_version_is_refused(tmp_path):
    first, _ = run(tmp_path, tag="1")
    state_path = first.output_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 99
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(PipelineError, match="state.json"):
        run(tmp_path, tag="2", previous_folder=first.output_dir)
