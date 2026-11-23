import json

import pytest

from catalogue.errors import PipelineError
from catalogue.publish import site as site_module
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


def test_places_hold_photographed_listings_and_every_stop_and_empty_values_stay_empty(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    names = [p["name"] for p in city["places"]]
    # photographed listings first by page order, then every other block stop (no photo)
    assert names == ["Fort 1", "Fort 2", "Fort 3", "Fort 4", "Gone Gate"]
    assert city["places"][0]["summary"]["text"] == "Fort one."
    assert city["places"][1]["summary"] is None  # never filled in


def test_blocks_carry_stops_and_cost_with_basis(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    (block,) = city["blocks"]
    assert block["id"] == "jaipur-full"
    assert [s["name"] for s in block["stops"]] == ["Fort 1", "Fort 2", "Fort 4", "Gone Gate"]
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
    assert sum(p["entries"] for p in doc["parts"]) == len(entries) == 12
    for part in doc["parts"]:
        # a part is under the target unless a single entry is bigger than the target
        assert (result.site_dir / V1 / part["file"]).stat().st_size < 4000


def test_the_index_over_its_limit_stops_the_build(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    limits = PublishLimits(max_files=100, max_file_bytes=10**6, max_index_bytes=100)
    with pytest.raises(PipelineError, match=r"index/in.json.*limit"):
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
    assert "/v1/index/*" in text  # the per-country index files of a world run


def test_data_files_are_revalidated_so_updates_reach_the_app(tmp_path):
    result, _ = _build(tmp_path)
    text = (result.site_dir / "_headers").read_text(encoding="utf-8")
    rules = {}
    path = None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            path = line
        elif line.strip().startswith("Cache-Control:"):
            rules[path] = line.split(":", 1)[1].strip()
    for path in ("/v1/cities/*", "/v1/attributions.json", "/v1/attributions/*"):
        assert rules[path] == "public, max-age=0, must-revalidate", path
    assert all("max-age=3600" not in v for v in rules.values())


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


def _rehash(folder, name):
    import hashlib

    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["outputs_sha256"][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_only_checked_blocks_and_single_city_days_are_published(tmp_path):
    result, _ = _build(tmp_path)
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    assert [b["id"] for b in city["blocks"]] == ["jaipur-full"]  # the flagged block is held back
    # a day with a flagged block, and a day that mixes two cities, are never published
    assert [d["id"] for d in city["days"]] == ["day:jaipur-full"]
    agra = _json(result.site_dir / V1 / "cities" / "agra.json")
    assert agra["days"] == []


def test_a_photo_without_a_licence_is_left_out_and_counted(tmp_path):
    result, _ = _build(tmp_path)
    assert result.totals["photos_left_out_no_credit"] == 1
    city = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    fort_4 = next(p for p in city["places"] if p["name"] == "Fort 4")
    assert fort_4["photo"] is None  # a stop of a block stays a place, photo left out


def test_a_photo_with_neither_author_nor_attribution_is_left_out():
    from catalogue.publish import build as b

    counts = b.Counter()
    row = {"photo": {**publishfixture.CREDIT_PHOTO, "author": "", "attribution": ""}}
    assert b.photo_of(row, counts) is None
    assert counts["photos_left_out_no_credit"] == 1


def test_credit_links_must_be_https():
    from catalogue.publish import build as b

    counts = b.Counter()
    row = {"photo": {**publishfixture.CREDIT_PHOTO, "description_url": "http://x/y"}}
    assert b.photo_of(row, counts) is None
    assert counts["photos_left_out_no_credit"] == 1


def test_attribution_entries_keep_the_history_link_of_summaries(tmp_path):
    result, _ = _build(tmp_path)
    _, entries = _entries(result.site_dir)
    summaries = [e for e in entries if e["kind"] == "summary"]
    assert summaries and all("history_url" in e for e in summaries)


def test_readiness_built_from_other_inputs_is_refused(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    photos = kwargs["photos_folder"]
    row = {"subject": "article", "page_id": 99, "ordinal": 0, "photo": None}
    with (photos / "photo_selection.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _rehash(photos, "photo_selection.jsonl")  # the folder is self-consistent, readiness is stale
    with pytest.raises(PipelineError, match="readiness was built from other photo_selection"):
        run_publish_static(**kwargs)


def test_checks_built_from_other_blocks_are_refused(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    path = kwargs["checks_folder"] / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["inputs_sha256"]["day_blocks.jsonl"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="auto-check was built from other day_blocks"):
        run_publish_static(**kwargs)


def test_prices_built_from_other_blocks_are_refused(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    path = kwargs["prices_folder"] / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["inputs_sha256"]["day_blocks.jsonl"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="price-bands was built from other day_blocks"):
        run_publish_static(**kwargs)


def test_two_published_cities_with_one_slug_are_refused(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    path = kwargs["readiness_folder"] / "city_readiness.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["slug"] == "agra":
            row["slug"] = "jaipur"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _fix_readiness_hash(path)
    with pytest.raises(PipelineError, match="slug"):
        run_publish_static(**kwargs)


def test_two_rows_for_one_key_are_refused():
    from catalogue.publish.run import _unique

    rows = [
        {"block_id": "b", "check_status": "checked"},
        {"block_id": "b", "check_status": "flagged"},
    ]
    with pytest.raises(PipelineError, match="block_checks.jsonl has more than one row"):
        _unique(rows, lambda r: r["block_id"], "block_checks.jsonl")
    assert _unique(rows[:1], lambda r: r["block_id"], "x") == {"b": rows[0]}


def test_every_written_file_is_size_checked(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    limits = PublishLimits(max_files=100, max_file_bytes=10**6, max_other_bytes=150)
    with pytest.raises(PipelineError, match=r"attributions.*150 bytes"):
        run_publish_static(**kwargs, limits=limits)


def test_a_part_is_measured_in_bytes_not_characters():
    from catalogue.publish.run import _chunks

    entry = {"city": "x", "note": "\u00e9" * 100}  # about 230 bytes, 130 characters
    assert len(_chunks([entry, entry, entry], target=300)) == 3


def test_a_failed_build_keeps_the_previous_site(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    first = run_publish_static(**kwargs)
    marker = first.site_dir / V1 / "manifest.json"
    before = marker.read_bytes()
    with pytest.raises(PipelineError):
        run_publish_static(**kwargs, limits=PublishLimits(max_files=3, max_file_bytes=10**6))
    assert marker.read_bytes() == before


def test_a_rebuild_leaves_no_stale_files(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    first = run_publish_static(**kwargs)
    (first.site_dir / V1 / "cities" / "stale.json").write_text("{}\n", encoding="utf-8")
    second = run_publish_static(**kwargs)
    assert not (second.site_dir / V1 / "cities" / "stale.json").exists()
    assert not list(second.output_dir.glob("site.*"))


def test_totals_count_published_cities_that_look_thin(tmp_path):
    result, _ = _build(tmp_path)
    assert result.totals["without_hero"] == 0
    assert result.totals["without_summary"] == 0
    assert result.totals["not_ready_published"] == 0


def test_an_old_http_licence_link_is_a_valid_credit():
    from catalogue.publish import build as b

    licence = "http://creativecommons.org/licenses/by-sa/3.0/"
    row = {"photo": {**publishfixture.CREDIT_PHOTO, "licence_url": licence}}
    photo = b.photo_of(row, b.Counter())
    assert photo is not None and photo["credit"]["licence_url"] == licence
    bad = {"photo": {**publishfixture.CREDIT_PHOTO, "licence_url": "javascript:alert(1)"}}
    assert b.photo_of(bad, b.Counter()) is None


def test_a_city_without_its_own_hero_uses_a_hero_quality_place_photo(tmp_path):
    result, _ = _build(tmp_path)
    agra = _json(result.site_dir / V1 / "cities" / "agra.json")
    assert agra["hero"]["caption"] == "Tomb 1"  # the one hero-quality photo among its places
    assert agra["hero"]["credit"]["author"] == "Gerd Eichmann"
    assert agra["hero"]["credit"]["licence"] == "CC BY-SA 4.0"
    index = {c["slug"]: c for c in _json(result.site_dir / V1 / "index.json")["cities"]}
    assert index["agra"]["hero"]["caption"] == "Tomb 1"
    jaipur = _json(result.site_dir / V1 / "cities" / "jaipur.json")
    assert jaipur["hero"]["caption"] is None  # its own article photo wins


def test_the_fallback_hero_is_the_best_scoring_credited_hero_quality_photo():
    from catalogue.publish import build as b

    def photo(name, score, hero=True, licence="CC BY-SA 4.0"):
        return {
            "hero_quality": hero,
            "score": score,
            "photo": {**publishfixture.CREDIT_PHOTO, "licence_name": licence, "commons_file": name},
        }

    def place(ordinal, name):
        return {"page_id": 7, "ordinal": ordinal, "name": name, "listing_key": f"see:{ordinal}"}

    rows = [place(1, "Low"), place(2, "Best"), place(3, "Plain"), place(4, "Unlicensed")]
    up = b.Upstream(
        place_listings={7: rows},
        photos={
            ("listing", 7, 1): photo("a.jpg", 1.0),
            ("listing", 7, 2): photo("b.jpg", 3.0),
            ("listing", 7, 3): photo("c.jpg", 9.0, hero=False),
            ("listing", 7, 4): photo("d.jpg", 9.5, licence=""),
        },
        summaries={},
        timezones={},
        blocks={},
        stops={},
        block_costs={},
        days={},
        bands={},
    )
    hero = b.fallback_hero(7, up)
    assert hero["caption"] == "Best"
    assert up.counts["photos_left_out_no_credit"] == 0  # looking for a hero never counts photos
    up.place_listings[7] = [place(3, "Plain"), place(4, "Unlicensed")]
    assert b.fallback_hero(7, up) is None  # nothing hero-quality with a credit: stays empty


BAD_AUTHORS = [
    "see above",
    "Unknown author",
    "John Smith ( talk )",
    "someone@example.org",
    "myself",
    "A" * 101,
    "",
]


def _up_with_photo(**changes):
    from catalogue.publish import build as b

    photo = {**publishfixture.CREDIT_PHOTO, **changes}
    return b.Upstream(
        place_listings={},
        photos={("listing", 7, 1): {"hero_quality": False, "photo": photo}},
        summaries={},
        timezones={},
        blocks={},
        stops={},
        block_costs={},
        days={},
        bands={},
    )


def _listing():
    return {"page_id": 7, "ordinal": 1, "name": "Fort", "listing_key": "see:1", "kind": "see"}


@pytest.mark.parametrize("author", BAD_AUTHORS)
def test_a_place_photo_that_fails_the_author_rule_is_left_out_and_counted(author):
    from catalogue.publish import build as b

    up = _up_with_photo(author=author, attribution=f"{author}, CC BY-SA 4.0")

    assert b._place(_listing(), up, is_stop=False) is None  # not photographed: not a place
    assert up.counts["photos_left_out_bad_author"] == 1
    assert up.counts["photos_left_out_no_credit"] == 0


@pytest.mark.parametrize("author", BAD_AUTHORS)
def test_a_stop_keeps_its_place_but_not_a_photo_that_fails_the_author_rule(author):
    from catalogue.publish import build as b

    up = _up_with_photo(author=author, attribution=f"{author}, CC BY-SA 4.0")
    place = b._place(_listing(), up, is_stop=True)

    assert place is not None and place["photo"] is None
    assert up.counts["photos_left_out_bad_author"] == 1


def test_a_place_photo_with_a_real_author_is_kept():
    from catalogue.publish import build as b

    up = _up_with_photo()

    assert b._place(_listing(), up, is_stop=False)["photo"]["credit"]["author"] == "Gerd Eichmann"
    assert up.counts["photos_left_out_bad_author"] == 0


def test_an_author_that_says_myself_may_not_be_a_hero():
    from catalogue.publish import hero

    rules = hero.load_hero_rules()
    row = {
        "hero_quality": True,
        "photo": {**publishfixture.CREDIT_PHOTO, "author": "myself", "attribution": "myself"},
    }

    assert hero.hero_problem(row, rules) == "author"


def test_the_site_root_has_a_small_landing_page_that_links_the_credits(tmp_path):
    result, _ = _build(tmp_path)
    page = (result.site_dir / "index.html").read_text(encoding="utf-8")

    assert "VoyageHub catalogue: city data for the VoyageHub app, with credits" in page
    assert 'href="/v1/attributions.json"' in page
    assert "<script" not in page and "http://" not in page and "@" not in page
    assert page == site_module.LANDING_PAGE
    assert len(page.encode("utf-8")) < 4096


def test_the_landing_page_is_the_same_in_every_build(tmp_path):
    first, kwargs = _build(tmp_path)
    second = run_publish_static(**{**kwargs, "out_dir": tmp_path / "publish2"})

    assert (first.site_dir / "index.html").read_bytes() == (
        second.site_dir / "index.html"
    ).read_bytes()


def test_a_photo_in_the_hero_rejection_list_is_no_hero_but_stays_a_place_photo(tmp_path):
    from catalogue.publish import build as b
    from catalogue.publish import hero

    heroes = tmp_path / "rejected_heroes.csv"
    heroes.write_text("commons_file,reason\nX.jpg,a bus stand\n", encoding="utf-8")
    rules = hero.load_hero_rules(heroes_path=heroes)
    row = {"hero_quality": True, "photo": publishfixture.CREDIT_PHOTO}

    assert hero.hero_problem(row, rules) == "override"
    assert b.photo_of(row, b.Counter(), rules) is not None  # still fine as a place photo


def test_the_committed_hero_rejection_list_loads_and_every_row_has_a_reason():
    from catalogue.photos.select import load_rejected_files
    from catalogue.publish import hero

    assert load_rejected_files(hero.HEROES_PATH)  # raises on a missing reason or a bad column
    assert not load_rejected_files(hero.HEROES_PATH) & load_rejected_files()  # kept apart
