import json

import pytest

from catalogue.discover.slugs import (
    RegistryEntry,
    SlugRequest,
    assign_slugs,
    load_registry,
    save_registry,
    slugify,
)
from catalogue.errors import PipelineError


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Jaipur", "jaipur"),
        ("São Paulo", "sao-paulo"),
        ("Zürich", "zurich"),
        ("Canberra/Acton", "canberra-acton"),
        ("Kochi (Kerala)", "kochi-kerala"),
        ("  --Odd   name!! ", "odd-name"),
        ("東京", ""),
    ],
)
def test_slugify_is_lowercase_ascii_and_hyphenated(title, slug):
    assert slugify(title) == slug


def request(page_id, title, iso="IN"):
    return SlugRequest(page_id=page_id, title=title, country_iso=iso)


def test_a_new_page_gets_its_plain_slug():
    registry, events = assign_slugs([request(7, "Jaipur")], {})

    assert registry[7] == RegistryEntry(slug="jaipur", title="Jaipur")
    assert events == []


def test_a_known_page_keeps_its_slug_on_a_rerun():
    first, _ = assign_slugs([request(7, "Jaipur")], {})

    second, events = assign_slugs([request(7, "Jaipur")], first)

    assert second == first
    assert events == []


def test_a_renamed_page_keeps_its_old_slug_and_records_the_new_title():
    first, _ = assign_slugs([request(7, "Bombay")], {})

    second, events = assign_slugs([request(7, "Mumbai")], first)

    assert second[7] == RegistryEntry(slug="bombay", title="Mumbai")
    assert [(e.kind, e.page_id, e.slug) for e in events] == [("rename", 7, "bombay")]
    assert "Bombay" in events[0].detail
    assert "Mumbai" in events[0].detail


def test_a_newcomer_that_collides_gets_the_country_suffix_and_the_old_slug_stays():
    existing, _ = assign_slugs([request(1, "Zurich", "CH")], {})

    registry, events = assign_slugs([request(2, "Zürich", "US")], existing)

    assert registry[1].slug == "zurich"
    assert registry[2].slug == "zurich-us"
    assert [(e.kind, e.page_id, e.slug) for e in events] == [("collision", 2, "zurich-us")]


def test_in_one_run_the_lower_page_id_gets_the_plain_slug():
    registry, _ = assign_slugs([request(9, "Zürich", "US"), request(3, "Zurich", "CH")], {})

    assert registry[3].slug == "zurich"
    assert registry[9].slug == "zurich-us"


def test_if_the_suffixed_slug_is_taken_too_the_page_id_is_added():
    existing, _ = assign_slugs([request(1, "Zurich", "CH"), request(2, "Zürich", "US")], {})

    registry, _ = assign_slugs([request(3, "Zurích", "US")], existing)

    assert registry[3].slug == "zurich-us-3"


def test_without_a_country_the_page_id_is_the_suffix():
    existing, _ = assign_slugs([request(1, "Zurich", "CH")], {})

    registry, events = assign_slugs([request(5, "Zürich", None)], existing)

    assert registry[5].slug == "zurich-5"
    assert events[0].kind == "collision"


def test_a_title_with_no_latin_letters_falls_back_to_the_page_id():
    registry, events = assign_slugs([request(42, "東京")], {})

    assert registry[42].slug == "place-42"
    assert events[0].kind == "fallback"


def test_slugs_of_pages_that_left_the_candidate_list_stay_reserved():
    existing, _ = assign_slugs([request(1, "Zurich", "CH")], {})

    registry, _ = assign_slugs([request(2, "Zürich", "US")], existing)

    assert registry[1].slug == "zurich"
    assert registry[2].slug != "zurich"


def test_the_input_registry_is_not_modified():
    existing, _ = assign_slugs([request(1, "Bombay")], {})
    before = dict(existing)

    assign_slugs([request(1, "Mumbai"), request(2, "Delhi")], existing)

    assert existing == before


def test_the_registry_survives_a_save_and_load(tmp_path):
    registry, _ = assign_slugs([request(7, "Jaipur"), request(8, "Agra")], {})
    path = tmp_path / "nested" / "slugs.json"

    save_registry(path, registry)

    assert load_registry(path) == registry
    assert not list(path.parent.glob("*.tmp"))


def test_a_missing_registry_file_means_an_empty_registry(tmp_path):
    assert load_registry(tmp_path / "slugs.json") == {}


@pytest.mark.parametrize("content", ["not json", "[]", '{"schema_version": 99, "entries": {}}'])
def test_a_damaged_registry_is_an_error_not_a_silent_reset(tmp_path, content):
    path = tmp_path / "slugs.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PipelineError, match="slug registry"):
        load_registry(path)


def test_the_saved_file_is_readable_json_keyed_by_page_id(tmp_path):
    path = tmp_path / "slugs.json"

    save_registry(path, {7: RegistryEntry(slug="jaipur", title="Jaipur")})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["entries"]["7"] == {"slug": "jaipur", "title": "Jaipur"}
