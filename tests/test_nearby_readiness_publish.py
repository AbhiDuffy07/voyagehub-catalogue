"""Readiness and publish-static read the same `nearby` places as photo-select, or refuse."""

import json

import pytest

from catalogue.errors import PipelineError
from catalogue.nearby.run import EXTRA_ORDINAL_BASE
from catalogue.publish.run import run_publish_static
from catalogue.readiness.run import run_readiness
from tests import publishfixture
from tests import readinessfixture as rf

EXTRA = EXTRA_ORDINAL_BASE + 777


def extra_place(owner, ordinal, name):
    return {
        "page_id": owner,
        "ordinal": ordinal,
        "owner_page_id": owner,
        "kind": "see",
        "name": name,
        "listing_key": f"see:{name.lower().replace(' ', '-')}~Q{ordinal - EXTRA_ORDINAL_BASE}",
        "duplicate_of": None,
        "lat": "26.95",
        "long": "75.81",
        "source": "wikidata_nearby",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "Wikidata contributors",
        "price": "",
        "price_status": "empty",
        "hours": "",
        "hours_status": "empty",
    }


def write_nearby(root, places, country="IN"):
    folder = root / "nearby"
    files = {
        "nearby_places.jsonl": rf.jsonl(folder / "nearby_places.jsonl", places),
        "nearby_candidates.jsonl": rf.jsonl(folder / "nearby_candidates.jsonl", []),
    }
    rf.manifest(folder, files, country=country, photo_candidates_manifest_sha256="x")
    return folder


def add_photos(photos_folder, extra_rows, nearby_folder):
    """Append photo-select rows and record the nearby places hash like photo-select does."""
    path = photos_folder / "photo_selection.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    digest = rf.jsonl(path, rows + extra_rows)
    places = rf.sha(nearby_folder / "nearby_places.jsonl") if nearby_folder else None
    rf.manifest(photos_folder, {"photo_selection.jsonl": digest}, nearby_places_sha256=places)


def geo_photo(pid, ordinal, hero=True):
    row = rf.photo("listing", pid, ordinal, hero=hero)
    row["photo"] = {
        **row["photo"],
        "commons_file": f"G{ordinal}.jpg",
        "sources": ["commons_geosearch"],
    }
    return row


# --- readiness ----------------------------------------------------------------------------------


def test_extra_places_with_a_photo_count_as_photographed_places(tmp_path):
    kwargs = rf.build(tmp_path)
    root = kwargs["photos_folder"].parent
    nearby = write_nearby(
        root,
        [extra_place(5, EXTRA, "Fort Kochi Museum"), extra_place(5, EXTRA + 1, "Dutch Palace")],
    )
    add_photos(
        kwargs["photos_folder"],
        [rf.photo("listing", 5, EXTRA), rf.photo("listing", 5, EXTRA + 1)],
        nearby,
    )
    result = run_readiness(**kwargs, nearby_folder=nearby)
    kochi = rf.rows(result)[5]
    assert kochi["photographed_places"] == 3  # one listing photo plus two extra places
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_sha256"]["nearby_places.jsonl"] == rf.sha(
        nearby / "nearby_places.jsonl"
    )


def test_a_geosearch_photo_counts_as_a_place_but_never_as_a_hero(tmp_path):
    kwargs = rf.build(tmp_path)
    root = kwargs["photos_folder"].parent
    nearby = write_nearby(root, [extra_place(5, EXTRA, "Fort Kochi Museum")])
    path = kwargs["photos_folder"] / "photo_selection.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r["page_id"] != 5]  # Kochi keeps only the geosearch photo
    rf.jsonl(path, rows)
    add_photos(kwargs["photos_folder"], [geo_photo(5, EXTRA)], nearby)
    kochi = rf.rows(run_readiness(**kwargs, nearby_folder=nearby))[5]
    assert kochi["photographed_places"] == 1
    assert kochi["hero_photos"] == 0


def test_without_nearby_nothing_changes(tmp_path):
    result, _ = rf.run(tmp_path)
    assert rf.rows(result)[5]["photographed_places"] == 1


def test_photo_select_with_nearby_but_readiness_without_it_is_refused(tmp_path):
    kwargs = rf.build(tmp_path)
    nearby = write_nearby(kwargs["photos_folder"].parent, [extra_place(5, EXTRA, "Museum")])
    add_photos(kwargs["photos_folder"], [rf.photo("listing", 5, EXTRA)], nearby)
    with pytest.raises(PipelineError) as raised:
        run_readiness(**kwargs)
    assert "nearby" in str(raised.value)


def test_other_nearby_places_than_photo_select_used_are_refused(tmp_path):
    kwargs = rf.build(tmp_path)
    root = kwargs["photos_folder"].parent
    nearby = write_nearby(root, [extra_place(5, EXTRA, "Museum")])
    add_photos(kwargs["photos_folder"], [rf.photo("listing", 5, EXTRA)], nearby)
    rf.jsonl(nearby / "nearby_places.jsonl", [extra_place(5, EXTRA, "Other")])
    with pytest.raises(PipelineError):
        run_readiness(**kwargs, nearby_folder=nearby)


def test_a_nearby_folder_photo_select_never_saw_is_refused(tmp_path):
    kwargs = rf.build(tmp_path)
    nearby = write_nearby(kwargs["photos_folder"].parent, [extra_place(5, EXTRA, "Museum")])
    with pytest.raises(PipelineError) as raised:
        run_readiness(**kwargs, nearby_folder=nearby)
    assert "photo-select" in str(raised.value)


# --- publish ------------------------------------------------------------------------------------


def _publish_with_extra(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    root = tmp_path / "world"
    place = {
        **extra_place(1, EXTRA, "Albert Hall Museum"),
        "listing_key": "see:albert-hall-museum~Q777",
    }
    nearby = write_nearby(root, [place])
    photo = rf.photo("listing", 1, EXTRA)
    photo["photo"] = {**publishfixture.CREDIT_PHOTO, "commons_file": "Albert.jpg"}
    add_photos(kwargs["photos_folder"], [photo], nearby)
    readiness = run_readiness(
        listings_folder=kwargs["listings_folder"],
        summaries_folder=kwargs["summaries_folder"],
        photos_folder=kwargs["photos_folder"],
        blocks_folder=kwargs["blocks_folder"],
        checks_folder=kwargs["checks_folder"],
        timezones_folder=kwargs["timezones_folder"],
        discover_folder=root / "discover",
        slugs_path=root / "slugs.json",
        out_dir=tmp_path / "readiness2",
        dump_date="20261101",
        today=rf.TODAY,
        country_iso="IN",
        nearby_folder=nearby,
    )
    return {**kwargs, "readiness_folder": readiness.output_dir}, nearby


def test_publish_shows_the_extra_place_and_credits_wikidata(tmp_path):
    kwargs, nearby = _publish_with_extra(tmp_path)
    result = run_publish_static(**kwargs, nearby_folder=nearby)
    city = json.loads((result.site_dir / "v1" / "cities" / "jaipur.json").read_text("utf-8"))
    extra = [p for p in city["places"] if p["key"] == "see:albert-hall-museum~Q777"]
    assert len(extra) == 1 and extra[0]["name"] == "Albert Hall Museum"
    assert extra[0]["photo"]["credit"]["source_url"].startswith("https://commons.wikimedia.org/")
    notices = json.loads((result.site_dir / "v1" / "attributions.json").read_text("utf-8"))[
        "notices"
    ]
    assert {
        "attribution": "Wikidata contributors",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    } in notices


def test_publish_refuses_to_run_without_the_nearby_folder_readiness_used(tmp_path):
    kwargs, _ = _publish_with_extra(tmp_path)
    with pytest.raises(PipelineError) as raised:
        run_publish_static(**kwargs)
    assert "nearby" in str(raised.value)
