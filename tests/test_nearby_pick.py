from collections import Counter
from dataclasses import replace

import pytest

from catalogue.nearby.pick import (
    GeoHit,
    KnownPlace,
    OtherPlace,
    accept_hits,
    nearest_city,
    photo_problem,
    place_words,
    same_place,
)
from catalogue.nearby.rules import load_nearby_rules
from catalogue.photos.commons import FileInfo
from catalogue.photos.rules import load_photo_rules
from catalogue.photos.select import load_selection_rules
from catalogue.publish.hero import load_hero_rules

RULES = load_nearby_rules()
# The name-free band as a mechanism (the committed value is 0 since rules version 2).
BAND = replace(RULES, name_free_radius_m=40)
SELECTION = load_selection_rules()
HERO = load_hero_rules()
PHOTO_RULES = load_photo_rules()
HERE = (26.9239, 75.8267)
# About 1 m of latitude in degrees.
M = 1 / 111_195


def hit(title, metres, north=None):
    north = metres if north is None else north
    return GeoHit(f"File:{title}", (HERE[0] + north * M, HERE[1]), float(metres))


def accept(hits, name="Hawa Mahal", others=(), rules=BAND):
    skipped = Counter()
    found = accept_hits(hits, place_words(name, SELECTION), others, rules, SELECTION, HERO, skipped)
    return [a.hit.title.removeprefix("File:") for a in found], skipped


def test_named_files_inside_the_radius_are_accepted_named_first_then_nearest():
    titles, _ = accept(
        [hit("Street 1.jpg", 10), hit("Hawa Mahal 2.jpg", 90), hit("Hawa Mahal 1.jpg", 30)]
    )
    assert titles == ["Hawa Mahal 1.jpg", "Hawa Mahal 2.jpg", "Street 1.jpg"]


def test_a_file_beyond_the_search_radius_is_never_taken():
    titles, skipped = accept([hit("Hawa Mahal.jpg", RULES.search_radius_m + 1)])
    assert titles == [] and skipped["far"] == 1


def test_an_unnamed_file_needs_to_be_inside_the_name_free_band():
    inside = BAND.name_free_radius_m
    titles, skipped = accept([hit("IMG 1.jpg", inside), hit("IMG 2.jpg", inside + 5)])
    assert titles == ["IMG 1.jpg"] and skipped["no_name_match"] == 1


def test_name_free_band_zero_means_a_name_match_is_always_required():
    strict = replace(RULES, name_free_radius_m=0)
    titles, skipped = accept([hit("IMG 1.jpg", 0), hit("Hawa Mahal.jpg", 0)], rules=strict)
    assert titles == ["Hawa Mahal.jpg"] and skipped["no_name_match"] == 1


def test_a_busy_point_takes_no_unnamed_file_but_still_takes_named_ones():
    # The Hawa Mahal probe: a thali, cloth work and a police officer at 0 m.
    hits = [hit(f"Rajasthani thali {i}.jpg", 0) for i in range(RULES.max_unnamed_files + 1)]
    titles, skipped = accept([*hits, hit("Hawa Mahal16.jpg", 0)])
    assert titles == ["Hawa Mahal16.jpg"]
    assert skipped["busy_point"] == RULES.max_unnamed_files + 1


def test_generic_words_are_no_name_match():
    titles, skipped = accept([hit("Hindu temple at dusk.jpg", 60)], name="Hindu Temple")
    assert titles == [] and skipped["no_name_match"] == 1


def test_drop_and_collage_words_in_the_file_name_are_skipped():
    titles, skipped = accept(
        [hit("Hawa Mahal map.jpg", 5), hit("Jaipur montage Hawa Mahal.jpg", 5)]
    )
    assert titles == [] and skipped["name_word"] == 2


def test_an_unnamed_file_nearer_another_place_is_ambiguous():
    other = OtherPlace((HERE[0] + 30 * M, HERE[1]), place_words("City Palace", SELECTION))
    titles, skipped = accept([hit("IMG 9.jpg", 20)], others=[other])
    assert titles == [] and skipped["ambiguous"] == 1


def test_a_file_named_for_this_place_only_survives_a_nearby_other_place():
    other = OtherPlace((HERE[0] + 30 * M, HERE[1]), place_words("City Palace", SELECTION))
    titles, _ = accept([hit("Hawa Mahal front.jpg", 20)], others=[other])
    assert titles == ["Hawa Mahal front.jpg"]


def test_a_file_named_for_both_places_is_ambiguous():
    other = OtherPlace((HERE[0] + 30 * M, HERE[1]), place_words("Mahal Cafe", SELECTION))
    titles, skipped = accept([hit("Hawa Mahal and Mahal Cafe.jpg", 20)], others=[other])
    assert titles == [] and skipped["ambiguous"] == 1


def test_another_place_far_away_does_not_make_a_file_ambiguous():
    other = OtherPlace((HERE[0] + 400 * M, HERE[1]), frozenset())
    titles, _ = accept([hit("IMG 1.jpg", 10)], others=[other])
    assert titles == ["IMG 1.jpg"]


# --- photo checks -------------------------------------------------------------------------------


def info(**overrides):
    base = dict(
        status="ok",
        title="File:Hawa Mahal.jpg",
        url="https://upload.wikimedia.org/a.jpg",
        description_url="https://commons.wikimedia.org/wiki/File:Hawa_Mahal.jpg",
        width=4000,
        height=3000,
        mime="image/jpeg",
        sha1="a" * 40,
        thumb_url="https://upload.wikimedia.org/thumb/a.jpg",
        thumb_width=1280,
        author="Ann",
        licence_code="cc-by-sa-4.0",
        licence_name="CC BY-SA 4.0",
        licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
        marks=(),
    )
    return FileInfo(**{**base, **overrides})


def photo(i):
    return {
        "commons_file": i.title.removeprefix("File:"),
        "description_url": i.description_url,
        "thumb_url": i.thumb_url,
        "thumb_width": i.thumb_width,
        "width": i.width,
        "height": i.height,
        "author": i.author,
        "licence_name": i.licence_name,
        "licence_url": i.licence_url,
        "attribution": f"{i.author}, {i.licence_name}, via Wikimedia Commons" if i.author else None,
    }


def problem(i):
    return photo_problem(i, photo(i), PHOTO_RULES, SELECTION, HERO)


def test_a_good_file_has_no_problem():
    assert problem(info()) is None


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"status": "missing"}, "file_missing"),
        ({"licence_code": "cc-by-nc-4.0"}, "licence_not_allowed"),
        ({"author": None}, "no_author"),
        ({"mime": "image/svg+xml"}, "not_an_image"),
        ({"thumb_url": None}, "no_thumb_url"),
        ({"title": "File:Hawa Mahal collage.jpg"}, "name_word"),
        ({"title": "File:Jaipur locator map.jpg"}, "name_word"),
        ({"width": 600, "height": 400}, "below_gate"),
        ({"width": 9000, "height": 2000}, "panorama"),
        ({"width": None}, "no_size"),
        ({"author": "Unknown author"}, "credit_or_author"),
        ({"author": "someone@example.invalid"}, "credit_or_author"),
    ],
)
def test_every_existing_photo_check_applies(overrides, reason):
    assert problem(info(**overrides)) == reason


def test_both_override_lists_apply():
    assert HERO.rejected, "the committed override lists are not empty"
    name = sorted(HERO.rejected)[0]
    assert problem(info(title=f"File:{name}")) == "rejected_list"


# --- extra places -------------------------------------------------------------------------------


def test_same_place_by_wikidata_id_name_and_distance():
    known = KnownPlace(HERE, place_words("Amber Fort", SELECTION), "Q1")
    near = (HERE[0] + 100 * M, HERE[1])
    assert same_place(HERE, frozenset(), "Q1", known, RULES)
    assert same_place(near, place_words("Amber Fort palace", SELECTION), "Q2", known, RULES)
    assert not same_place(near, place_words("Birla Mandir", SELECTION), "Q2", known, RULES)
    assert same_place((HERE[0] + 20 * M, HERE[1]), frozenset(), "Q3", known, RULES)
    far = (HERE[0] + 400 * M, HERE[1])
    assert not same_place(far, place_words("Amber Fort", SELECTION), "Q2", known, RULES)


def test_nearest_city_breaks_ties_by_page_id():
    assert nearest_city(HERE, {7: (HERE[0] + 0.1, HERE[1]), 3: (HERE[0] - 0.1, HERE[1])}) == 3
    assert nearest_city(HERE, {7: HERE, 3: (HERE[0] + 1, HERE[1])}) == 7
    assert nearest_city(HERE, {}) is None
