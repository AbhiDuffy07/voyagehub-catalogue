import pytest

from catalogue.errors import PipelineError
from catalogue.photos.select import choose, load_rejected_files, load_selection_rules, name_words

RULES = load_selection_rules()
FEATURED = "Featured pictures on Wikimedia Commons"


def cand(
    name,
    *,
    sources=("wikidata_p18",),
    rank=None,
    marks=(),
    width=4000,
    height=3000,
    mime="image/jpeg",
    sha1=None,
    flags=(),
    usable=True,
):
    return {
        "commons_file": name,
        "sources": sorted(sources),
        "p18_rank": rank,
        "quality_marks": list(marks),
        "width": width,
        "height": height,
        "mime": mime,
        "sha1": sha1 or f"sha-{name}",
        "flags": sorted(flags),
        "usable": usable,
    }


def pick(rows, rejected=frozenset(), words=None):
    """`words` are the subject's own name words; None skips the category-relatedness rule, which
    only its own tests exercise."""
    return choose(rows, RULES, rejected, words)


# --- rules and overrides -----------------------------------------


def test_committed_rules_load_with_the_owner_numbers():
    assert RULES.version >= 1
    assert RULES.min_long_side == 1024
    assert RULES.hero_long_side == 2000
    assert RULES.max_aspect == 2.5


def test_a_bad_rules_file_is_a_pipeline_error(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("rules_version = 1\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="rules.toml"):
        load_selection_rules(path)


def test_rejected_files_need_a_reason_and_normalise_names(tmp_path):
    path = tmp_path / "rejected.csv"
    path.write_text(
        "# comment\ncommons_file,reason\nFile:Bad_photo.jpg,shows a person\n", encoding="utf-8"
    )
    assert load_rejected_files(path) == frozenset({"Bad photo.jpg"})


def test_missing_rejected_file_means_none(tmp_path):
    assert load_rejected_files(tmp_path / "nope.csv") == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "commons_file,reason\nA.jpg,\n",
        "name,reason\nA.jpg,why\n",
        "commons_file,reason\nA.jpg,why\nA_jpg.jpg,x\nA.jpg,again\n",
    ],
)
def test_bad_rejected_files_are_refused(tmp_path, text):
    path = tmp_path / "rejected.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PipelineError):
        load_rejected_files(path)


def test_the_committed_rejected_list_loads():
    from pathlib import Path

    assert isinstance(load_rejected_files(Path("overrides/rejected_photos.csv")), frozenset)


# --- tiers -----------------------------------------


def test_p18_beats_featured_beats_quality_beats_valued():
    p18 = cand("P18.jpg")
    featured = cand("F.jpg", sources=("commons_category",), marks=[FEATURED])
    quality = cand("Q.jpg", sources=("commons_category",), marks=["Quality images"])
    valued = cand("V.jpg", sources=("commons_category",), marks=["Valued images"])
    valued_date = cand(
        "V2.jpg", sources=("commons_category",), marks=["Valued images sorted by promotion date"]
    )
    assert pick([valued, quality, featured, p18]).chosen["commons_file"] == "P18.jpg"
    assert pick([valued, quality, featured]).chosen["commons_file"] == "F.jpg"
    assert pick([valued, quality]).chosen["commons_file"] == "Q.jpg"
    assert pick([valued_date]).chosen["commons_file"] == "V2.jpg"
    assert pick([valued]).tier == "valued"
    assert [pick(rows).tier for rows in ([p18], [featured], [quality])] == [
        "p18",
        "featured",
        "quality",
    ]


def test_a_p18_that_is_also_featured_stays_tier_one():
    both = cand("B.jpg", marks=[FEATURED])
    featured = cand(
        "F.jpg", sources=("commons_category",), marks=[FEATURED], width=6000, height=4000
    )
    assert pick([featured, both]).chosen["commons_file"] == "B.jpg"


def test_preferred_p18_beats_a_bigger_normal_p18():
    preferred = cand("Pref.jpg", rank="preferred", width=1100, height=800, flags=["multiple_p18"])
    big = cand("Big.jpg", rank="normal", width=5000, height=3500, flags=["multiple_p18"])
    choice = pick([big, preferred])
    assert choice.chosen["commons_file"] == "Pref.jpg"
    assert "multiple_p18_chosen_by_score" not in choice.flags


def test_several_normal_p18_are_ranked_by_score_and_say_so():
    small = cand("Small.jpg", rank="normal", width=1200, height=800, flags=["multiple_p18"])
    big = cand("Big.jpg", rank="normal", width=3000, height=2000, flags=["multiple_p18"])
    choice = pick([small, big])
    assert choice.chosen["commons_file"] == "Big.jpg"
    assert "multiple_p18_chosen_by_score" in choice.flags


# --- score within a tier -----------------------------------------


def test_landscape_three_two_beats_portrait_at_equal_size():
    landscape = cand("L.jpg", width=3000, height=2000)
    portrait = cand("P.jpg", width=2000, height=3000)
    assert pick([portrait, landscape]).chosen["commons_file"] == "L.jpg"


def test_a_much_bigger_portrait_can_still_win():
    landscape = cand("L.jpg", width=1100, height=733)
    portrait = cand("P.jpg", width=2600, height=3900)
    assert pick([landscape, portrait]).chosen["commons_file"] == "P.jpg"


def test_ties_break_on_file_name_in_any_input_order():
    one = cand("A.jpg")
    two = cand("B.jpg")
    assert pick([two, one]).chosen["commons_file"] == "A.jpg"
    assert pick([one, two]).chosen["commons_file"] == "A.jpg"


def test_alternatives_are_the_next_two_distinct_files():
    rows = [cand(n, width=4000 - 100 * i, height=2667 - 70 * i) for i, n in enumerate("ABCD")]
    choice = pick(rows)
    assert choice.chosen["commons_file"] == "A"
    assert choice.alternatives == ["B", "C"]


def test_the_same_image_under_two_names_is_one_candidate():
    rows = [
        cand("A.jpg", sha1="same"),
        cand("B.jpg", sha1="same"),
        cand("C.jpg", width=1500, height=1000),
    ]
    choice = pick(rows)
    assert choice.chosen["commons_file"] == "A.jpg"
    assert choice.alternatives == ["C.jpg"]


# --- the size gate -----------------------------------------


@pytest.mark.parametrize(
    "width,height,passes",
    [
        (1024, 700, True),
        (1023, 700, False),
        (700, 1024, True),
        (700, 1023, False),
        (1024, 1024, True),
    ],
)
def test_long_side_gate_edges(width, height, passes):
    choice = pick([cand("A.jpg", width=width, height=height)])
    assert (choice.chosen is not None) is passes
    if not passes:
        assert choice.flags == {"only_below_gate"}
        assert choice.covered is True


def test_portrait_passes_and_carries_the_flag():
    choice = pick([cand("P.jpg", width=1400, height=2000)])
    assert choice.chosen["commons_file"] == "P.jpg"
    assert "portrait" in choice.flags and choice.portrait is True


def test_a_square_or_landscape_photo_is_not_portrait():
    assert pick([cand("S.jpg", width=2000, height=2000)]).portrait is False


def test_hero_quality_is_two_thousand_on_the_long_side():
    assert pick([cand("A.jpg", width=2000, height=1000)]).hero_quality is True
    assert pick([cand("A.jpg", width=1999, height=1000)]).hero_quality is False
    assert pick([cand("A.jpg", width=1200, height=2000)]).hero_quality is True


def test_a_small_candidate_loses_to_a_big_one_even_in_a_lower_tier():
    small_p18 = cand("S.jpg", width=900, height=600)
    big_quality = cand("Q.jpg", sources=("commons_category",), marks=["Quality images"])
    assert pick([small_p18, big_quality]).chosen["commons_file"] == "Q.jpg"


# --- drops -----------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Jaipur map 1936.jpg",
        "North karnataka locator map.jpg",
        "National Flag of India.png",
        "YSS-Logo.png",
        "Coat of arms of Goa.png",
        "Bhutia Busty - Emblem.jpg",
        "Floor plan diagram.jpg",
        "WV banner Jodhpur.jpg",
        "Official seal.png",
    ],
)
def test_maps_flags_logos_arms_diagrams_are_dropped_by_name(name):
    choice = pick([cand(name)])
    assert choice.chosen is None and choice.flags == {"only_excluded"}


def test_name_words_match_whole_words_only():
    for name in ("Mapleton Fort.jpg", "Flagstaff House.jpg", "Planet view.jpg", "Sealed door.jpg"):
        assert pick([cand(name)]).chosen is not None, name


def test_svg_and_other_types_are_dropped():
    assert pick([cand("A.svg", mime="image/svg+xml")]).flags == {"only_excluded"}
    assert pick([cand("A.tif", mime="image/tiff")]).chosen is None


def test_panoramas_over_two_and_a_half_to_one_are_dropped():
    assert pick([cand("Wide.jpg", width=5000, height=2000)]).chosen is not None  # exactly 2.5
    assert pick([cand("Wide.jpg", width=5001, height=2000)]).flags == {"only_excluded"}


def test_an_override_file_is_never_chosen():
    rows = [cand("Bad photo.jpg"), cand("Next.jpg", width=2000, height=1300)]
    choice = pick(rows, rejected=frozenset({"Bad photo.jpg"}))
    assert choice.chosen["commons_file"] == "Next.jpg"
    only = pick([cand("Bad photo.jpg")], rejected=frozenset({"Bad photo.jpg"}))
    assert only.chosen is None and only.flags == {"only_excluded"}


# --- no photo -----------------------------------------


def test_no_candidate_at_all():
    choice = pick([])
    assert choice.chosen is None and choice.flags == {"no_candidate"}
    assert choice.covered is False


def test_only_unusable_candidates():
    choice = pick([cand("A.jpg", usable=False, flags=["licence_not_allowed"])])
    assert choice.chosen is None and choice.flags == {"no_usable_candidate"}


def test_unusable_candidates_never_win_over_usable_ones():
    rows = [cand("Good.jpg", width=1100, height=800), cand("Bad.jpg", usable=False)]
    assert pick(rows).chosen["commons_file"] == "Good.jpg"


def test_the_candidate_flags_travel_with_the_choice():
    choice = pick([cand("A.jpg", flags=["p18_after_facts"])])
    assert "p18_after_facts" in choice.flags


def test_covered_means_a_candidate_survived_the_drops_before_the_gate():
    assert pick([cand("A.jpg", width=800, height=500)]).covered is True
    assert pick([cand("A map.jpg")]).covered is False
    assert pick([cand("A.jpg", width=2000, height=1500)]).covered is True


# --- category-only candidates must be about the subject ---------------------------------


def category_only(name, **kw):
    return cand(name, sources=("commons_category",), marks=["Quality images"], **kw)


def test_name_words_are_letters_of_four_or_more_case_and_accent_folded():
    assert name_words("Tiger Hill, Darjeeling (Bhāratpur) 12.jpg") == {
        "tiger",
        "hill",
        "darjeeling",
        "bharatpur",
    }
    assert name_words("A of the 7 és") == set()


def test_a_category_only_photo_needs_a_word_of_the_subjects_own_name():
    rosefinch = category_only("Dark-breasted Rosefinch.jpg")
    choice = pick([rosefinch], words=frozenset({"tiger", "hill", "darjeeling"}))
    assert choice.chosen is None and choice.flags == {"only_unrelated_category"}
    assert choice.covered is False


def test_a_category_only_photo_that_names_the_place_is_kept():
    fort = category_only("Amer Fort at dusk.jpg")
    assert pick([fort], words=frozenset({"amer", "fort"})).chosen is not None


def test_a_p18_photo_needs_no_name_match():
    p18 = cand("Unrelated name.jpg")
    assert pick([p18], words=frozenset({"nothing"})).chosen["commons_file"] == "Unrelated name.jpg"


def test_a_file_that_is_both_p18_and_category_needs_no_name_match():
    both = cand("Unrelated name.jpg", sources=("commons_category", "wikidata_p18"))
    assert pick([both], words=frozenset({"nothing"})).chosen is not None


def test_an_unrelated_category_photo_never_beats_or_replaces_a_related_one():
    unrelated = category_only("Iceberg in North Star Bay.jpg", width=6000, height=4000)
    related = category_only("Gurgaon skyline.jpg", width=1500, height=1000)
    choice = pick([unrelated, related], words=frozenset({"gurgaon"}))
    assert choice.chosen["commons_file"] == "Gurgaon skyline.jpg"


def test_unrelated_category_photos_do_not_count_as_covered_for_the_gate_numbers():
    small_unrelated = category_only("Iceberg.jpg", width=800, height=600)
    assert pick([small_unrelated], words=frozenset({"gurgaon"})).flags == {
        "only_unrelated_category"
    }


def test_generic_words_do_not_count_as_a_name_match():
    palace_of_winds = category_only("Palace of Winds.jpg")
    choice = pick([palace_of_winds], words=frozenset({"lake"}))  # "Lake Palace" -> lake, palace
    assert choice.flags == {"only_unrelated_category"}
    assert "palace" in RULES.generic_words and "temple" in RULES.generic_words


def test_a_distinctive_word_still_matches_when_generic_ones_are_also_present():
    photo = category_only("Amer Fort wall.jpg")
    words = name_words("Amer Fort", 4, RULES.generic_words)
    assert words == {"amer"}
    assert pick([photo], words=frozenset(words)).chosen is not None


def test_name_words_can_drop_generic_words():
    assert name_words("Hindu Temple of Joshimath", 4, frozenset({"hindu", "temple"})) == {
        "joshimath"
    }


def test_a_subject_named_only_with_generic_words_matches_no_category_photo():
    words = name_words("Hindu Temple", 4, RULES.generic_words)
    assert words == set()
    photo = category_only("Hindu temple interior.jpg")
    assert pick([photo], words=frozenset(words)).flags == {"only_unrelated_category"}
