from itertools import permutations

from catalogue.listings.keys import KeyInput, assign_keys


def item(page_id, ordinal, name="dutch-palace", *, scope=3, kind="see", **fields):
    return KeyInput(
        scope=scope,
        base=f"{kind}:{name}",
        page_id=page_id,
        ordinal=ordinal,
        wikidata=fields.get("wikidata", ""),
        lat=fields.get("lat", ""),
        long=fields.get("long", ""),
        address=fields.get("address", ""),
    )


def keys(*items):
    return assign_keys(items)


def test_a_name_used_once_keeps_the_plain_key():
    assert keys(item(3, 1)) == {(3, 1): "see:dutch-palace"}


def test_repeated_names_are_told_apart_by_wikidata_id_first():
    result = keys(item(3, 1, wikidata="Q1", lat="9.9", long="76.2"), item(3, 2, wikidata="q2"))

    assert result == {(3, 1): "see:dutch-palace~Q1", (3, 2): "see:dutch-palace~Q2"}


def test_without_wikidata_rounded_coordinates_are_used_and_tiny_differences_do_not_matter():
    a = item(3, 1, lat="9.93123", long="76.26001")
    same_place = item(3, 2, lat="9.931229", long="76.260012")
    elsewhere = item(3, 3, lat="10.5", long="77.1")

    result = keys(a, same_place, elsewhere)

    assert result[(3, 3)].startswith("see:dutch-palace~") and result[(3, 3)].count("~") == 1
    # a and same_place share a coordinate label, so they need the ordinal as the last resort
    assert result[(3, 1)] != result[(3, 2)]
    assert result[(3, 1)].rsplit("~", 1)[1] == "1" and result[(3, 2)].rsplit("~", 1)[1] == "2"
    assert result[(3, 1)].rsplit("~", 2)[0] == result[(3, 2)].rsplit("~", 2)[0]


def test_without_coordinates_the_address_is_used_ignoring_case_and_punctuation():
    first = item(3, 1, address="1 Main Road, Kochi")
    second = item(3, 2, address="2 Beach Road")
    twin = item(4, 1, address="1  main road kochi")

    result = keys(first, second, twin)

    assert result[(3, 2)].count("~") == 1
    assert result[(3, 1)] != result[(4, 1)]  # same address twice: ordinal as the last resort


def test_with_nothing_to_tell_them_apart_the_ordinal_decides_in_page_then_ordinal_order():
    result = keys(item(5, 1), item(3, 2), item(3, 1))

    assert result == {
        (3, 1): "see:dutch-palace~1",
        (3, 2): "see:dutch-palace~2",
        (5, 1): "see:dutch-palace~3",
    }


def test_one_member_with_no_label_among_labelled_ones_keeps_a_distinct_key():
    result = keys(item(3, 1, wikidata="Q1"), item(3, 2))

    assert result[(3, 1)] == "see:dutch-palace~Q1"
    assert result[(3, 2)] == "see:dutch-palace"


def test_the_same_name_under_different_owners_or_kinds_is_not_a_repeat():
    result = keys(item(3, 1, scope=3), item(8, 1, scope=9), item(3, 3, kind="eat"))

    assert set(result.values()) == {"see:dutch-palace", "eat:dutch-palace"}


def test_keys_do_not_depend_on_the_order_the_listings_are_read_in():
    items = [
        item(3, 1, wikidata="Q1"),
        item(3, 2, lat="9.9", long="76.2"),
        item(4, 1, lat="9.9", long="76.2"),
        item(4, 2),
        item(5, 1),
        item(6, 1, address="x"),
    ]
    expected = assign_keys(items)
    for ordering in permutations(items):
        assert assign_keys(ordering) == expected
