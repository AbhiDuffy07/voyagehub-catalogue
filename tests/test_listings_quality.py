import random

import pytest

from catalogue.errors import PipelineError
from catalogue.listings.quality import (
    DupInput,
    QualityRules,
    coordinate_flags,
    find_duplicates,
    gap_group,
    load_rules,
    normalised_name,
    shape_flags,
)

RULES = QualityRules(version=3, far_km={"city": 100.0, "park": 60.0})


# --- rules file ---------------------------------------------------------------------------------


def write(tmp_path, text):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_rules_are_read_with_their_reasons(tmp_path):
    path = write(
        tmp_path,
        'rules_version = 2\n[coordinates.far_km.city]\nkm = 100\nreason = "p99 is 62 km"\n'
        '[coordinates.far_km.park]\nkm = 60.5\nreason = "gap after 50 km"\n',
    )

    rules = load_rules(path)

    assert (rules.version, rules.far_km) == (2, {"city": 100.0, "park": 60.5})


@pytest.mark.parametrize(
    "body",
    [
        '[coordinates.far_km.city]\nkm = 100\nreason = ""\n',  # a rule needs a written reason
        "[coordinates.far_km.city]\nkm = 100\n",
        '[coordinates.far_km.city]\nkm = 0\nreason = "x"\n',
        '[coordinates.far_km.city]\nkm = "far"\nreason = "x"\n',
        '[coordinates.far_km.city]\nreason = "x"\n',
    ],
)
def test_a_rule_without_a_reason_or_a_positive_number_is_refused(tmp_path, body):
    with pytest.raises(PipelineError):
        load_rules(write(tmp_path, "rules_version = 1\n" + body))


def test_a_missing_version_or_file_is_refused(tmp_path):
    with pytest.raises(PipelineError):
        load_rules(write(tmp_path, '[coordinates.far_km.city]\nkm = 1\nreason = "x"\n'))
    with pytest.raises(PipelineError):
        load_rules(tmp_path / "nothing.toml")


# --- coordinates --------------------------------------------------------------------------------


def flags(lat, long, distance, page_type="city"):
    return coordinate_flags(lat, long, distance_km=distance, page_type=page_type, rules=RULES)


def test_no_coordinates_is_missing_not_invalid():
    assert flags("", "", None) == ["coord_missing"]
    assert flags(" ", "", None) == ["coord_missing"]


@pytest.mark.parametrize(("lat", "long"), [("x", "1"), ("9.9", ""), ("0", "0"), ("95", "10")])
def test_unreadable_or_impossible_coordinates_are_invalid(lat, long):
    assert flags(lat, long, None) == ["coord_invalid"]


def test_valid_coordinates_on_a_page_without_usable_geo_are_reported_as_unchecked():
    assert flags("9.9", "76.2", None) == ["coord_unchecked"]


def test_far_means_more_than_the_page_types_threshold():
    assert flags("9.9", "76.2", 99.9) == []
    assert flags("9.9", "76.2", 100.0) == []
    assert flags("9.9", "76.2", 100.1) == ["coord_far"]
    assert flags("9.9", "76.2", 61.0, page_type="park") == ["coord_far"]


def test_a_page_type_without_a_threshold_is_not_distance_checked():
    assert flags("9.9", "76.2", 5000.0, page_type="region") == []
    assert flags("9.9", "76.2", 5000.0, page_type=None) == []


# --- shape checks -------------------------------------------------------------------------------


def shape(**fields):
    base = {"name": "A place", "url": "", "email": "", "wikidata": ""}
    return shape_flags({**base, **fields})


@pytest.mark.parametrize("url", ["https://example.org/a?b=1", "http://x.in", "https://a.b.c/d"])
def test_good_urls_are_not_flagged(url):
    assert shape(url=url) == []


@pytest.mark.parametrize(
    "url",
    [
        "www.example.org",
        "example",
        "ftp://x.org",
        "https://",
        "https://exa mple.org",
        "javascript:alert(1)",
        "[https://x.org]",
        "https://localhost",
    ],
)
def test_bad_urls_are_flagged(url):
    assert shape(url=url) == ["url_invalid"]


def test_an_empty_url_is_not_flagged():
    assert shape(url="  ") == []


@pytest.mark.parametrize("email", ["a@b.example", "first.last@mail.co.in"])
def test_good_emails_are_not_flagged(email):
    assert shape(email=email) == []


@pytest.mark.parametrize(
    "email", ["a@b", "a b@c.org", "@x.org", "a@@b.org", "mailto:a@b.org", "a@b.org, c@d"]
)
def test_bad_emails_are_flagged(email):
    assert shape(email=email) == ["email_invalid"]


def test_a_missing_name_and_a_name_with_markup_are_flagged():
    assert shape(name="") == ["name_missing"]
    assert shape(name="  ") == ["name_missing"]
    for name in ("[[Fort]]", "{{x}}", "<b>Fort</b>", "''Fort''"):
        assert shape(name=name) == ["name_markup"], name


@pytest.mark.parametrize("wikidata", ["Q", "42", "Q0", "Qabc", "Q12 3", "Q-1", "P42"])
def test_a_malformed_wikidata_id_is_flagged(wikidata):
    assert shape(wikidata=wikidata) == ["wikidata_invalid"]


@pytest.mark.parametrize("wikidata", ["Q42", "q42", " Q42 ", ""])
def test_well_formed_or_empty_wikidata_ids_are_not_flagged(wikidata):
    assert shape(wikidata=wikidata) == []


def test_several_problems_are_listed_in_a_fixed_order():
    assert shape(name="", url="x", email="y", wikidata="z") == [
        "name_missing",
        "url_invalid",
        "email_invalid",
        "wikidata_invalid",
    ]


# --- the Wikidata gap ---------------------------------------------------------------------------


def test_listings_with_usable_coordinates_have_no_gap():
    assert gap_group(has_coordinates=True, wikidata="Q1", wikipedia="x") is None


def test_the_three_groups_of_listings_without_coordinates():
    assert gap_group(has_coordinates=False, wikidata="Q42", wikipedia="") == "has_wikidata"
    assert gap_group(has_coordinates=False, wikidata="Q42", wikipedia="Fort") == "has_wikidata"
    assert gap_group(has_coordinates=False, wikidata="", wikipedia="Fort") == "wikipedia_only"
    assert gap_group(has_coordinates=False, wikidata="Q-x", wikipedia="Fort") == "wikipedia_only"
    assert gap_group(has_coordinates=False, wikidata="", wikipedia="") == "neither"
    assert gap_group(has_coordinates=False, wikidata="bad", wikipedia=" ") == "neither"


# --- duplicates ---------------------------------------------------------------------------------


def dup(
    page_id,
    ordinal,
    name="fort",
    *,
    scope=1,
    kind="see",
    wikidata="",
    point=None,
    address="",
    from_marker=False,
):
    return DupInput(scope, kind, name, wikidata, point, address, page_id, ordinal, from_marker)


def test_a_name_normalises_case_accents_and_punctuation_but_not_other_scripts():
    assert normalised_name("Café  du Monde!") == normalised_name("cafe DU monde") == "cafe du monde"
    assert normalised_name("ताज महल") == "ताज महल"  # the vowel signs of other scripts stay
    assert normalised_name("Straße") == "strasse"
    assert normalised_name("!!") == ""


def test_the_same_wikidata_id_is_a_duplicate_with_the_earlier_row_as_original():
    result = find_duplicates([dup(1, 1, wikidata="Q1"), dup(1, 2, wikidata="Q1")])

    assert result.duplicates == {(1, 2): ((1, 1), "wikidata")}


def test_close_coordinates_are_a_duplicate_distant_ones_are_not():
    near = find_duplicates([dup(1, 1, point=(9.9, 76.2)), dup(1, 2, point=(9.9005, 76.2))])
    far = find_duplicates([dup(1, 1, point=(9.9, 76.2)), dup(1, 2, point=(9.905, 76.2))])

    assert near.duplicates == {(1, 2): ((1, 1), "name_and_coords")}  # about 55 m
    assert far.duplicates == {}  # about 550 m: another branch with the same name


def test_the_same_name_with_nothing_to_compare_is_a_low_confidence_duplicate():
    both_blank = find_duplicates([dup(1, 1), dup(1, 2)])
    one_blank = find_duplicates([dup(1, 1, point=(9.9, 76.2)), dup(1, 2)])

    assert both_blank.duplicates == {(1, 2): ((1, 1), "name_only")}
    assert one_blank.duplicates == {(1, 2): ((1, 1), "name_only")}


def test_two_different_wikidata_ids_are_different_places_whatever_the_name():
    result = find_duplicates(
        [dup(1, 1, wikidata="Q1", point=(9.9, 76.2)), dup(1, 2, wikidata="Q2", point=(9.9, 76.2))]
    )

    assert result.duplicates == {}


def test_other_kinds_and_other_owners_are_not_duplicates():
    result = find_duplicates([dup(1, 1), dup(1, 2, kind="eat"), dup(2, 1, scope=2)])

    assert result.duplicates == {}


def test_the_same_wikidata_under_two_kinds_is_the_same_place_not_a_duplicate():
    result = find_duplicates(
        [
            dup(1, 1, name="fort", wikidata="Q1"),
            dup(1, 2, name="fort cafe", kind="eat", wikidata="Q1"),
        ]
    )

    assert result.duplicates == {}
    assert result.same_place == {(1, 2): (1, 1)}


def test_a_chain_points_at_the_earliest_earlier_match_that_fits():
    result = find_duplicates(
        [
            dup(1, 1, point=(9.9000, 76.2)),
            dup(1, 2, point=(9.9010, 76.2)),  # 111 m from the first
            dup(1, 3, point=(9.9020, 76.2)),  # 111 m from the second, 222 m from the first
        ]
    )

    assert result.duplicates == {
        (1, 2): ((1, 1), "name_and_coords"),
        (1, 3): ((1, 2), "name_and_coords"),
    }


def test_the_result_does_not_depend_on_the_input_order():
    items = [
        dup(1, 1, wikidata="Q1"),
        dup(1, 2, wikidata="Q1"),
        dup(2, 1, point=(9.9, 76.2)),
        dup(3, 1, point=(9.9002, 76.2)),
        dup(3, 2),
        dup(4, 1, kind="eat", wikidata="Q1"),
    ]
    expected = find_duplicates(items)
    for seed in range(20):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 (a test shuffle, not security)
        assert find_duplicates(shuffled) == expected


def test_an_unnamed_listing_is_never_a_duplicate_of_another_unnamed_one():
    result = find_duplicates([dup(1, 1, name=""), dup(1, 2, name="")])

    assert result.duplicates == {}


def test_the_committed_rules_file_loads_and_every_threshold_has_its_reason():
    rules = load_rules()

    assert rules.version >= 1
    assert {"city", "district", "park"} <= set(rules.far_km)
    assert set(rules.reasons) == set(rules.far_km)
    assert all(len(reason) > 40 for reason in rules.reasons.values())


def test_a_list_of_good_emails_is_not_flagged_but_one_bad_one_in_it_is():
    assert shape(email="a@b.org, c@d") == ["email_invalid"]
    assert shape(email="a@b.org, c@d.org") == []
    assert shape(email="a@b.org ; c@d.org") == []
    assert shape(email="a@b.org,c@d.org") == []


def test_the_same_name_with_the_same_address_is_a_duplicate_on_that_basis():
    result = find_duplicates([dup(1, 1, address="a1"), dup(1, 2, address="a1")])

    assert result.duplicates == {(1, 2): ((1, 1), "name_and_address")}


def test_the_same_name_at_different_addresses_is_another_branch_not_a_duplicate():
    result = find_duplicates([dup(1, 1, address="a1"), dup(1, 2, address="a2")])

    assert result.duplicates == {}


def test_an_address_on_one_side_only_is_not_enough_to_compare():
    result = find_duplicates([dup(1, 1, address="a1"), dup(1, 2)])

    assert result.duplicates == {(1, 2): ((1, 1), "name_only")}


def test_coordinates_decide_before_the_address_does():
    far = find_duplicates(
        [dup(1, 1, address="a1", point=(9.9, 76.2)), dup(1, 2, address="a1", point=(9.95, 76.2))]
    )

    assert far.duplicates == {}


def test_a_marker_row_is_never_the_original_of_a_full_listing():
    result = find_duplicates([dup(1, 1, from_marker=True), dup(1, 5)])

    assert result.duplicates == {(1, 1): ((1, 5), "name_only")}


def test_a_very_long_email_is_checked_in_linear_time():
    import time

    start = time.perf_counter()
    assert shape(email="a" * 80000) == ["email_invalid"]
    assert shape(email="a@" + "b." * 40000 + "c") == []
    assert time.perf_counter() - start < 2


def test_the_rules_loader_reports_a_malformed_file_as_a_pipeline_error(tmp_path):
    with pytest.raises(PipelineError):
        load_rules(write(tmp_path, 'rules_version = 1\ncoordinates = "x"\n'))
    with pytest.raises(PipelineError):
        load_rules(write(tmp_path, "rules_version = 1\n[coordinates]\nfar_km = 5\n"))
