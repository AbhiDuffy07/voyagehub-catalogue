import json

import pytest
import respx

from catalogue.errors import PipelineError
from catalogue.nearby.run import EXTRA_ORDINAL_BASE, run_nearby
from catalogue.photos.select_run import run_photo_select
from tests.nearbyfixture import (
    COMMONS,
    TODAY,
    WIKIDATA,
    build,
    fake_commons_api,
    fake_wikidata_api,
    sha,
)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def world(client, tmp_path):
    folders = build(tmp_path)
    with respx.mock:
        respx.get(WIKIDATA).mock(side_effect=fake_wikidata_api())
        respx.get(COMMONS).mock(side_effect=fake_commons_api())
        result = run_nearby(client, **folders, sleep=lambda s: None)
    return folders, result.output_dir


def select(folders, nearby_dir, tmp_path):
    return run_photo_select(
        photos_folder=folders["photos_folder"],
        enrich_folder=folders["enrich_folder"],
        out_dir=tmp_path / "pass2",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        nearby_folder=nearby_dir,
    )


def test_the_second_pass_chooses_nearby_candidates_for_new_subjects(world, tmp_path):
    folders, nearby_dir = world
    result = select(folders, nearby_dir, tmp_path)
    chosen = {
        (r["page_id"], r["ordinal"]): r
        for r in rows(result.output_dir / "photo_selection.jsonl")
        if r["photo"]
    }
    hawa = chosen[(1, 1)]
    assert hawa["photo"]["commons_file"] == "Hawa Mahal16.jpg"
    assert hawa["photo"]["sources"] == ["commons_geosearch"]
    assert hawa["tier"] == "other" and hawa["listing_key"] == "see:hawa-mahal"
    albert = chosen[(1, EXTRA_ORDINAL_BASE + 2001)]
    assert albert["tier"] == "p18" and albert["wikidata_id"] == "Q2001"
    assert albert["photo"]["commons_file"] == "Albert Hall.jpg"


def test_the_manifest_records_the_nearby_folder(world, tmp_path):
    folders, nearby_dir = world
    result = select(folders, nearby_dir, tmp_path)
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["nearby_manifest_sha256"] == sha(nearby_dir / "manifest.json")
    assert manifest["nearby_places_sha256"] == sha(nearby_dir / "nearby_places.jsonl")


def test_without_a_nearby_folder_nothing_changes(world, tmp_path):
    folders, _ = world
    result = select(folders, None, tmp_path)
    assert all(
        r["ordinal"] < EXTRA_ORDINAL_BASE for r in rows(result.output_dir / "photo_selection.jsonl")
    )
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["nearby_places_sha256"] is None


def test_a_changed_nearby_file_is_refused(world, tmp_path):
    folders, nearby_dir = world
    with (nearby_dir / "nearby_candidates.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(PipelineError) as raised:
        select(folders, nearby_dir, tmp_path)
    assert "nearby" in str(raised.value)


def test_nearby_output_from_other_photo_candidates_is_refused(world, tmp_path):
    folders, nearby_dir = world
    manifest_path = nearby_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["photo_candidates_manifest_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError) as raised:
        select(folders, nearby_dir, tmp_path)
    assert "nearby" in str(raised.value)
