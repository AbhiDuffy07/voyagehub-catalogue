import json

import pytest

from catalogue.errors import PipelineError
from catalogue.publish.limits import PublishLimits
from catalogue.publish.run import run_publish_static
from tests import publishfixture

V1 = "v1"


def _build(tmp_path, **overrides):
    kwargs = publishfixture.build(tmp_path)
    kwargs.update(overrides)
    return run_publish_static(**kwargs), kwargs


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_only_published_cities_with_a_slug_get_a_file(tmp_path):
    result, _ = _build(tmp_path)
    cities = sorted(p.name for p in (result.site_dir / V1 / "cities").iterdir())
    assert cities == ["agra.json", "jaipur.json"]
    assert result.totals["cities"] == 2


def test_city_file_has_summary_credit_timezone_and_tier(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    assert city["slug"] == "jaipur"
    assert city["tier"] == "plannable"
    assert city["summary"]["text"] == "Jaipur is pink."
    credit = city["summary"]["credit"]
    assert credit["licence"] == "CC BY-SA 4.0"
    assert credit["source_url"].startswith("https://en.wikipedia.org/")
    assert city["timezone"] == {"name": "Asia/Kolkata", "basis": "coordinates"}


def test_every_photo_keeps_author_licence_and_link(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    photos = [p["photo"] for p in city["places"] if p["photo"]] + [city["hero"]]
    assert len(photos) == 4  # three photographed places and the hero
    for photo in photos:
        credit = photo["credit"]
        assert credit["author"] == "Gerd Eichmann"
        assert credit["licence"] == "CC BY-SA 4.0"
        assert credit["licence_url"].startswith("https://")
        assert credit["source_url"].startswith("https://commons.wikimedia.org/")


def test_places_are_photographed_listings_and_empty_values_stay_empty(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    names = [p["name"] for p in city["places"]]
    assert names == ["Fort 1", "Fort 2", "Fort 3"]  # Fort 4 has no photo
    assert city["places"][0]["summary"]["text"] == "Fort one."
    assert city["places"][1]["summary"] is None  # never filled in


def test_blocks_carry_stops_and_cost_with_basis(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    (block,) = city["blocks"]
    assert block["id"] == "jaipur-full"
    assert [s["name"] for s in block["stops"]] == ["Fort 1", "Fort 2"]
    foreigner = block["cost"]["foreigner"]
    assert foreigner["basis"] == "2 stop(s) with no price"
    assert foreigner["total_low_minor"] is None
    assert block["cost"]["label"] == "estimate"
    assert city["days"][0]["block_ids"] == ["jaipur-full"]
    (band,) = city["price_bands"]
    assert band["label"] == "approximate"
    assert band["basis"] == "all"


def test_index_lists_cities_with_hero_and_tier(tmp_path):
    result, _ = _build(tmp_path)
    index = _json(result.site_dir / V1 / "index.json")
    by_slug = {c["slug"]: c for c in index["cities"]}
    assert set(by_slug) == {"jaipur", "agra"}
    assert by_slug["agra"]["tier"] == "browse_only"
    assert by_slug["jaipur"]["hero"]["thumb_url"] == "https://thumb.example/X.jpg"
    assert by_slug["jaipur"]["hero"]["credit"]["author"] == "Gerd Eichmann"


def _entries(site_dir):
    doc = _json(site_dir / V1 / "attributions.json")
    entries = []
    for part in doc["parts"]:
        entries += _json(site_dir / V1 / part["file"])["entries"]
    return doc, entries


def test_attributions_list_every_photo_and_summary(tmp_path):
    result, _ = _build(tmp_path)
    doc, entries = _entries(result.site_dir)
    kinds = [e["kind"] for e in entries]
    assert kinds.count("photo") == 8  # hero + 3 photographed places for each city
    assert kinds.count("summary") == 3  # two city summaries and one place summary
    for entry in entries:
        assert entry["licence"] and entry["source_url"] and entry["city"] in ("jaipur", "agra")
    assert doc["notices"]  # time zone data and Wikivoyage place data are credited too


def test_attributions_split_into_parts_under_the_size_target(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    limits = PublishLimits(max_files=100, max_file_bytes=10**6, attribution_part_bytes=2000)
    result = run_publish_static(**kwargs, limits=limits)
    doc, entries = _entries(result.site_dir)
    assert len(doc["parts"]) > 1
    assert sum(p["entries"] for p in doc["parts"]) == len(entries) == 11
    for part in doc["parts"]:
        # a part is under the target unless a single entry is bigger than the target
        assert (result.site_dir / V1 / part["file"]).stat().st_size < 4000


def test_the_index_over_its_limit_stops_the_build(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    limits = PublishLimits(max_files=100, max_file_bytes=10**6, max_index_bytes=100)
    with pytest.raises(PipelineError, match="index.json.*sharding"):
        run_publish_static(**kwargs, limits=limits)


def test_manifest_hashes_match_files_and_version_is_deterministic(tmp_path):
    import hashlib

    kwargs = publishfixture.build(tmp_path)
    first = run_publish_static(**kwargs)
    manifest = _json(first.site_dir / V1 / "manifest.json")
    for name, info in manifest["files"].items():
        data = (first.site_dir / V1 / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"]
        assert len(data) == info["bytes"]
    assert "manifest.json" not in manifest["files"]
    second = run_publish_static(**{**kwargs, "out_dir": tmp_path / "publish2"})
    assert _json(second.site_dir / V1 / "manifest.json")["version"] == manifest["version"]


def test_headers_file_sets_cache_rules(tmp_path):
    result, _ = _build(tmp_path)
    text = (result.site_dir / "_headers").read_text(encoding="utf-8")
    assert "/v1/index.json" in text and "/v1/manifest.json" in text


def test_a_slugless_published_city_is_skipped_and_counted(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    path = kwargs["readiness_folder"] / "city_readiness.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["slug"] == "agra":
            row["slug"] = None
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _fix_readiness_hash(path)
    result = run_publish_static(**kwargs)
    assert result.totals["cities"] == 1
    assert result.totals["skipped_no_slug"] == 1


def _fix_readiness_hash(path):
    import hashlib

    manifest_path = path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["city_readiness.jsonl"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_an_edited_input_is_refused(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    with (kwargs["prices_folder"] / "block_costs.jsonl").open("a", encoding="utf-8") as f:
        f.write("{}\n")
    with pytest.raises(PipelineError, match="block_costs.jsonl does not match"):
        run_publish_static(**kwargs)


def test_file_count_limit_stops_the_build(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    with pytest.raises(PipelineError, match="files"):
        run_publish_static(**kwargs, limits=PublishLimits(max_files=3, max_file_bytes=10**6))


def test_file_size_limit_stops_the_build(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    with pytest.raises(PipelineError, match="bytes"):
        run_publish_static(**kwargs, limits=PublishLimits(max_files=100, max_file_bytes=200))


def test_a_broken_schema_blocks_all_output(tmp_path, monkeypatch):
    from catalogue.publish import build as build_module

    original = build_module.build_city

    def broken(*args, **kwargs):
        city = original(*args, **kwargs)
        city["tier"] = "gold"  # not an allowed tier
        return city

    monkeypatch.setattr(build_module, "build_city", broken)
    kwargs = publishfixture.build(tmp_path)
    with pytest.raises(PipelineError, match="schema"):
        run_publish_static(**kwargs)
    assert not (tmp_path / "publish").exists()
