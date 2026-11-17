"""index.json stays one file while it fits; above the limit it is split by country."""

import json

import pytest

from catalogue.errors import PipelineError
from catalogue.publish.index import index_files
from catalogue.publish.run import run_publish_static
from tests import publishfixture

V1 = "v1"
LIMIT = 2 * 1024 * 1024


def fake_city(i, iso):
    slug = f"town-number-{i:06d}-{(iso or 'xx').lower()}"
    return {
        "slug": slug,
        "title": f"Town Number {i}",
        "country_iso": iso,
        "tier": "browse_only",
        "hero": {
            "thumb_url": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{slug}.jpg/800px-{slug}.jpg",
            "thumb_width": 800,
            "width": 4000,
            "height": 3000,
            "credit": {
                "author": "Some Photographer",
                "licence": "CC BY-SA 4.0",
                "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "source_url": f"https://commons.wikimedia.org/wiki/File:{slug}.jpg",
                "attribution": "Photo by Some Photographer, CC BY-SA 4.0",
                "history_url": None,
            },
        },
        "file": f"/v1/cities/{slug}.json",
    }


def world(countries=226, per_country=60):
    isos = [f"{chr(65 + a)}{chr(65 + b)}" for a in range(26) for b in range(26)][:countries]
    cities = [
        fake_city(n * 1000 + i, iso) for n, iso in enumerate(isos) for i in range(per_country)
    ]
    cities.append(fake_city(999999, None))  # a city with no country
    return sorted(cities, key=lambda c: c["slug"])


def test_a_small_index_stays_one_flat_file():
    cities = world(3, 5)

    files = index_files(cities, LIMIT)

    assert list(files) == ["index.json"]
    assert json.loads(files["index.json"])["cities"] == cities


def test_a_world_sized_index_over_2_mib_is_split_by_country_and_each_file_fits():
    cities = world()
    flat = len(json.dumps(cities, separators=(",", ":")).encode())
    assert flat > LIMIT, "the fake world must really be over the limit"

    files = index_files(cities, LIMIT)

    top = json.loads(files["index.json"])
    assert "cities" not in top and top["schema_version"] == 1
    assert len(top["shards"]) == 227  # 226 countries and the unknown one
    assert all(len(data) <= LIMIT for data in files.values())
    assert {s["file"].removeprefix("/v1/") for s in top["shards"]} == set(files) - {"index.json"}
    seen = []
    for shard in top["shards"]:
        doc = json.loads(files[shard["file"].removeprefix("/v1/")])
        assert shard["cities"] == len(doc["cities"])
        assert {c["country_iso"] for c in doc["cities"]} == {shard["country_iso"]}
        seen += doc["cities"]
    assert sorted(seen, key=lambda c: c["slug"]) == cities  # nothing lost, nothing doubled


def test_shard_files_are_named_by_lowercase_country_and_unknown_for_none():
    files = index_files(world(), LIMIT)

    assert "index/aa.json" in files and "index/unknown.json" in files
    top = json.loads(files["index.json"])
    assert [s["country_iso"] for s in top["shards"]] == sorted(
        (s["country_iso"] for s in top["shards"]), key=lambda c: (c is None, c or "")
    )


def test_the_output_is_deterministic():
    assert index_files(world(), LIMIT) == index_files(list(reversed(world())), LIMIT)


def test_a_single_country_over_the_limit_stops_with_a_clear_message():
    cities = [fake_city(i, "US") for i in range(40)]

    with pytest.raises(PipelineError, match=r"index/us\.json.*limit"):
        index_files(cities, 2000)


# --- through the whole build -------------------------------------------------------------------


def test_the_flat_index_is_unchanged_when_it_fits(tmp_path):
    kwargs = publishfixture.build(tmp_path)

    result = run_publish_static(**kwargs)

    doc = json.loads((result.site_dir / V1 / "index.json").read_text(encoding="utf-8"))
    assert set(doc) == {"schema_version", "cities"}
    assert not (result.site_dir / V1 / "index").exists()


def test_a_sharded_index_is_written_and_listed_in_the_manifest_by_the_build(tmp_path, monkeypatch):
    """The fixture world has one country, so give its two cities two countries on the way into the
    index and use a limit only the whole index exceeds."""
    from catalogue.publish import run as run_module

    real = run_module.index_files

    def two_countries(cities, max_bytes):
        moved = [{**c, "country_iso": "US" if i else "IN"} for i, c in enumerate(cities)]
        return real(moved, 700)

    monkeypatch.setattr(run_module, "index_files", two_countries)
    kwargs = publishfixture.build(tmp_path)

    result = run_publish_static(**kwargs)

    top = json.loads((result.site_dir / V1 / "index.json").read_text(encoding="utf-8"))
    assert [s["country_iso"] for s in top["shards"]] == ["IN", "US"]
    for shard in top["shards"]:
        assert (result.site_dir / shard["file"].lstrip("/")).is_file()
    manifest = json.loads((result.site_dir / V1 / "manifest.json").read_text(encoding="utf-8"))
    assert {"index.json", "index/in.json", "index/us.json"} <= set(manifest["files"])
