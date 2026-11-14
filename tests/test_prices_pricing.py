from datetime import date

import pytest

from catalogue.errors import PipelineError
from catalogue.prices.pricing import build_bands, classify_listing, load_price_rules

RULES = load_price_rules()
DUMP = date(2026, 11, 1)


def entry(amount=None, *, audience=None, currency="INR", unit=None, label=None, free=False, **kw):
    minor = None if amount is None else amount * 100
    row = {
        "amount": None if amount is None else str(amount),
        "amount_minor": minor,
        "amount_min_minor": None,
        "amount_max_minor": None,
        "audience": audience,
        "currency": currency,
        "currency_ambiguous": False,
        "free": free,
        "label": label,
        "unit": unit,
    }
    row.update(kw)
    return row


def listing(entries, *, kind="see", edit="2026-03-04", status="parsed", free=False, **kw):
    row = {
        "kind": kind,
        "page_id": 1,
        "ordinal": 1,
        "owner_page_id": 7,
        "price": "text",
        "price_status": status,
        "price_is_free": free,
        "price_entries": entries,
        "lastedit": edit or "",
        "lastedit_date": edit or None,
        "lastedit_status": "parsed" if edit else "missing",
        "lastedit_precision": "day" if edit and len(edit) == 10 else ("year" if edit else None),
        "lastedit_year": int(edit[:4]) if edit else None,
    }
    row.update(kw)
    return row


def test_committed_rules_load():
    assert RULES.version == 1 and RULES.fresh_years == 2 and RULES.min_prices == 3
    assert "see" in RULES.band_kinds and RULES.meals_full_day == 2


def test_a_missing_rules_file_is_refused(tmp_path):
    with pytest.raises(PipelineError):
        load_price_rules(tmp_path / "nope.toml")


def test_a_price_edited_within_two_years_is_exact_with_its_tiers():
    row = listing([entry(50, audience="resident"), entry(300, audience="foreigner")])
    price = classify_listing(row, DUMP, RULES)
    assert price.status == "exact"
    assert price.currency == "INR"
    assert price.tiers == {"resident": (5000, 5000), "foreigner": (30000, 30000)}


def test_an_untiered_or_adult_amount_is_the_adult_tier():
    assert classify_listing(listing([entry(100)]), DUMP, RULES).tiers == {"adult": (10000, 10000)}
    assert classify_listing(listing([entry(100, audience="adult")]), DUMP, RULES).tiers == {
        "adult": (10000, 10000)
    }


def test_a_price_older_than_two_years_is_never_exact():
    price = classify_listing(listing([entry(100)], edit="2020-05-01"), DUMP, RULES)
    assert price.status == "not_fresh" and price.age == "older" and price.tiers == {}


def test_an_undated_price_is_never_exact():
    price = classify_listing(listing([entry(100)], edit=None), DUMP, RULES)
    assert price.status == "not_fresh" and price.age == "undated"


def test_the_two_year_boundary_is_the_same_day_two_years_ago():
    assert classify_listing(listing([entry(1)], edit="2024-11-01"), DUMP, RULES).status == "exact"
    assert (
        classify_listing(listing([entry(1)], edit="2024-10-31"), DUMP, RULES).status == "not_fresh"
    )


def test_a_year_only_date_that_straddles_the_cutoff_is_not_exact():
    price = classify_listing(listing([entry(1)], edit="2024"), DUMP, RULES)
    assert price.status == "not_fresh" and price.age == "unclear"


def test_partial_unparsed_and_label_only_prices_are_never_numbers():
    for status in ("partial", "unparsed", "label_only"):
        price = classify_listing(listing([entry(100)], status=status), DUMP, RULES)
        assert price.status == "unreadable" and price.tiers == {}


def test_no_price_is_none():
    price = classify_listing(listing([], status="empty"), DUMP, RULES)
    assert price.status == "none"


def test_a_fresh_free_listing_is_exactly_free():
    price = classify_listing(listing([entry(free=True)], free=True), DUMP, RULES)
    assert price.status == "exact" and price.tiers == {"adult": (0, 0)}


def test_a_label_only_free_listing_is_exactly_free():
    price = classify_listing(listing([], status="label_only", free=True), DUMP, RULES)
    assert price.status == "exact" and price.tiers == {"adult": (0, 0)} and price.currency is None


def test_camera_fees_and_other_units_are_left_out():
    row = listing([entry(40, label="camera"), entry(900, unit="for 2"), entry(100)])
    assert classify_listing(row, DUMP, RULES).tiers == {"adult": (10000, 10000)}


def test_different_amounts_in_one_tier_are_ambiguous_and_unreadable():
    row = listing([entry(200), entry(600)])
    assert classify_listing(row, DUMP, RULES).status == "unreadable"
    row = listing([entry(400, audience="resident"), entry(300, audience="resident")])
    assert classify_listing(row, DUMP, RULES).status == "unreadable"


def test_an_untiered_amount_next_to_visitor_tiers_is_ambiguous_and_unreadable():
    row = listing([entry(400, audience="resident"), entry(1200), entry(200)])
    assert classify_listing(row, DUMP, RULES).status == "unreadable"


def test_the_same_amount_twice_is_one_price():
    price = classify_listing(listing([entry(100), entry(100)]), DUMP, RULES)
    assert price.tiers == {"adult": (10000, 10000)}


def test_an_explicit_adult_price_next_to_a_foreigner_price_is_fine():
    row = listing([entry(100, audience="adult"), entry(500, audience="foreigner")])
    assert classify_listing(row, DUMP, RULES).tiers == {
        "adult": (10000, 10000),
        "foreigner": (50000, 50000),
    }


def test_a_from_or_about_price_is_not_exact_but_still_feeds_the_band():
    for qualifier in ("from", "approx"):
        price = classify_listing(listing([entry(500, qualifier=qualifier)]), DUMP, RULES)
        assert price.status == "qualified" and price.tiers == {}
        assert price.band_values == {"resident": 50000, "foreigner": 50000}


def test_a_listing_with_only_unusable_entries_is_unreadable():
    row = listing([entry(900, unit="for 2")])
    assert classify_listing(row, DUMP, RULES).status == "unreadable"


def test_sleep_prices_count_per_night_only():
    row = listing([entry(2000, unit="per night")], kind="sleep")
    assert classify_listing(row, DUMP, RULES).tiers == {"adult": (200000, 200000)}
    row = listing([entry(2000, unit="per person")], kind="sleep")
    assert classify_listing(row, DUMP, RULES).status == "unreadable"


def test_a_range_keeps_both_ends():
    ranged = entry(None, amount_min_minor=10000, amount_max_minor=20000)
    price = classify_listing(listing([ranged]), DUMP, RULES)
    assert price.tiers == {"adult": (10000, 20000)}


def test_an_ambiguous_or_missing_currency_is_unreadable():
    for bad in (entry(100, currency=None), entry(100, currency_ambiguous=True)):
        assert classify_listing(listing([bad]), DUMP, RULES).status == "unreadable"


def test_two_currencies_in_one_listing_are_unreadable():
    row = listing([entry(100), entry(5, currency="USD", audience="foreigner")])
    assert classify_listing(row, DUMP, RULES).status == "unreadable"


def banded(values, *, kind="see", edit="2026-03-04", city=7, currency="INR"):
    return [
        classify_listing(
            listing([entry(v, currency=currency)], kind=kind, edit=edit, owner_page_id=city),
            DUMP,
            RULES,
        )
        for v in values
    ]


def test_a_band_is_the_median_with_the_middle_of_the_spread():
    bands = build_bands(banded([10, 20, 30, 40, 50]), RULES)
    band = bands[(7, "see", "INR", "foreigner")]
    assert band["median_minor"] == 3000
    assert (band["low_minor"], band["high_minor"]) == (2000, 4000)
    assert band["label"] == "approximate"


def test_fewer_than_three_prices_give_no_band():
    assert build_bands(banded([10, 20]), RULES) == {}


def test_fresh_prices_alone_make_the_band_when_there_are_enough():
    old = banded([1000, 2000, 3000], edit="2019-01-01")
    fresh = banded([10, 20, 30])
    band = build_bands(old + fresh, RULES)[(7, "see", "INR", "foreigner")]
    assert band["basis"] == "fresh" and band["median_minor"] == 2000
    assert (band["n_fresh"], band["n_older"], band["n_undated"]) == (3, 3, 0)


def test_too_few_fresh_prices_pool_with_older_and_undated_ones():
    pool = (
        banded([10], edit="2026-03-04") + banded([20], edit="2019-01-01") + banded([30], edit=None)
    )
    band = build_bands(pool, RULES)[(7, "see", "INR", "foreigner")]
    assert band["basis"] == "all"
    assert (band["n_fresh"], band["n_older"], band["n_undated"]) == (1, 1, 1)
    assert band["oldest_edit"] == "2019-01-01" and band["newest_edit"] == "2026-03-04"


def test_free_listings_count_as_zero_in_the_band():
    pool = [classify_listing(listing([entry(free=True)], free=True), DUMP, RULES)] * 2
    band = build_bands(pool + banded([100]), RULES)[(7, "see", "INR", "foreigner")]
    assert band["median_minor"] == 0 and band["high_minor"] == 5000


def test_bands_are_per_city_kind_currency_and_visitor_group():
    pool = banded([1, 2, 3]) + banded([1, 2, 3], city=8) + banded([1, 2, 3], kind="eat")
    pool += banded([1, 2, 3], currency="USD")
    groups = ("resident", "foreigner")
    assert set(build_bands(pool, RULES)) == {
        (city, kind, currency, group)
        for city, kind, currency in (
            (7, "see", "INR"),
            (8, "see", "INR"),
            (7, "eat", "INR"),
            (7, "see", "USD"),
        )
        for group in groups
    }


def test_a_visitor_tier_feeds_only_its_own_groups_band():
    tiered = [
        classify_listing(
            listing([entry(50 + i, audience="resident"), entry(300 + i, audience="foreigner")]),
            DUMP,
            RULES,
        )
        for i in range(3)
    ]
    bands = build_bands(tiered, RULES)
    assert bands[(7, "see", "INR", "resident")]["median_minor"] == 5100
    assert bands[(7, "see", "INR", "foreigner")]["median_minor"] == 30100


def test_a_single_tier_listing_feeds_that_group_only():
    only = [classify_listing(listing([entry(300, audience="foreigner")]), DUMP, RULES)] * 3
    assert set(build_bands(only, RULES)) == {(7, "see", "INR", "foreigner")}


def test_an_untiered_price_is_the_fallback_for_a_group_without_its_own_tier():
    pool = banded([100, 100]) + [
        classify_listing(listing([entry(300, audience="foreigner")]), DUMP, RULES)
    ]
    bands = build_bands(pool, RULES)
    assert bands[(7, "see", "INR", "foreigner")]["median_minor"] == 10000
    assert (7, "see", "INR", "resident") not in bands


def test_a_kind_without_a_band_is_ignored():
    assert build_bands(banded([1, 2, 3], kind="buy"), RULES) == {}


def test_a_range_listing_feeds_the_band_with_its_midpoint():
    ranged = entry(None, amount_min_minor=10000, amount_max_minor=30000)
    pool = [classify_listing(listing([ranged]), DUMP, RULES)] * 3
    assert build_bands(pool, RULES)[(7, "see", "INR", "foreigner")]["median_minor"] == 20000
