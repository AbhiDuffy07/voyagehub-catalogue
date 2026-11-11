import pytest

from catalogue.listings.prices import parse_price
from catalogue.listings.reference import CurrencyInfo, Reference

REFERENCE = Reference(
    country_currency={
        "IN": CurrencyInfo("INR", 2),
        "JP": CurrencyInfo("JPY", 0),
        "KW": CurrencyInfo("KWD", 3),
        "US": CurrencyInfo("USD", 2),
        "DE": CurrencyInfo("EUR", 2),
        "KP": CurrencyInfo("KPW", None),
    },
    demonyms={"IN": frozenset({"indian"}), "JP": frozenset({"japanese"})},
)


def price(raw, country="IN"):
    return parse_price(raw, country_iso=country, reference=REFERENCE)


def one(raw, country="IN"):
    result = price(raw, country)
    assert len(result.entries) == 1, (raw, result)
    return result.entries[0]


def test_an_empty_price_is_empty():
    result = price("  ")

    assert (result.status, result.entries, result.is_free, result.tier) == (
        "empty",
        [],
        False,
        None,
    )


def test_a_symbol_and_an_amount_in_minor_units():
    entry = one("₹300")

    assert (entry["amount_minor"], entry["currency"], entry["currency_basis"]) == (
        30000,
        "INR",
        "text",
    )
    assert entry["amount"] == "300"
    assert price("₹300").status == "parsed"


@pytest.mark.parametrize(
    "raw", ["Rs 5", "Rs. 5", "Rs.5", "rs5", "INR 5", "5 INR", "₹5", "{{INR|5}}"]
)
def test_rupee_forms(raw):
    entry = one(raw)

    assert (entry["amount_minor"], entry["currency"], entry["currency_basis"]) == (
        500,
        "INR",
        "text",
    )


def test_the_entry_fee_text_around_a_price_is_not_a_label():
    entry = one("Entry fee: ₹300")

    assert (entry["audience"], entry["audience_label"], entry["label"]) == (None, None, None)


def test_a_visitor_category_after_the_amount_and_a_date_that_is_not_an_amount():
    result = price("₹300 for foreigners as of February 2020")
    entry = result.entries[0]

    assert result.status == "parsed"
    assert (entry["amount_minor"], entry["audience"], entry["audience_label"]) == (
        30000,
        "foreigner",
        "foreigners",
    )


def test_tiers_with_labels_before_the_amounts():
    result = price("Adult: ₹10, Foreigner: ₹100")

    assert [(e["audience"], e["amount_minor"]) for e in result.entries] == [
        ("adult", 1000),
        ("foreigner", 10000),
    ]
    assert result.status == "parsed"


def test_a_demonym_means_resident_only_in_that_country():
    inside = price("₹50 (Indian), ₹500 (foreigner)")
    outside = price("Rs 50 (Indian)", country="JP")

    assert [(e["audience"], e["audience_label"]) for e in inside.entries] == [
        ("resident", "Indian"),
        ("foreigner", "foreigner"),
    ]
    assert outside.entries[0]["audience"] is None
    assert outside.entries[0]["audience_label"] == "Indian"


def test_regional_groups_and_unknown_labels_stay_labels_without_an_audience():
    result = price("Indian: ₹50; SAARC: ₹300; Others: ₹500")

    assert [(e["audience"], e["audience_label"]) for e in result.entries] == [
        ("resident", "Indian"),
        (None, "SAARC"),
        (None, "Others"),
    ]


def test_a_typo_in_a_label_is_not_fuzzy_matched():
    entry = one("Foriegner: ₹100")

    assert (entry["audience"], entry["audience_label"]) == (None, "Foriegner")


@pytest.mark.parametrize(
    ("raw", "audience"),
    [
        ("Adults ₹50", "adult"),
        ("Children ₹20", "child"),
        ("Kids ₹20", "child"),
        ("Student ₹10", "student"),
        ("Senior citizens ₹10", "senior"),
        ("Locals ₹10", "resident"),
        ("Residents ₹10", "resident"),
        ("Foreign nationals ₹100", "foreigner"),
    ],
)
def test_keyword_classes(raw, audience):
    assert one(raw)["audience"] == audience


def test_camera_and_video_fees_are_labels_not_audiences():
    result = price("₹50; camera ₹100; video ₹200")

    assert [(e["audience"], e["label"], e["amount_minor"]) for e in result.entries] == [
        (None, None, 5000),
        (None, "camera", 10000),
        (None, "video", 20000),
    ]


def test_ages_in_brackets_are_not_amounts():
    result = price("Adults ₹50, children (5-12) ₹20")

    assert [(e["audience"], e["amount_minor"]) for e in result.entries] == [
        ("adult", 5000),
        ("child", 2000),
    ]
    assert result.status == "parsed"


def test_for_two_and_per_unit_words():
    assert one("₹250 for 2")["unit"] == "for 2"
    assert one("₹2,000 per night")["unit"] == "per night"
    assert one("₹100 per person")["unit"] == "per person"
    assert one("₹100")["unit"] is None


def test_a_range_gives_a_minimum_and_a_maximum():
    entry = one("₹1825-2425")

    assert (entry["amount_minor"], entry["amount_min_minor"], entry["amount_max_minor"]) == (
        None,
        182500,
        242500,
    )
    assert (entry["amount_min"], entry["amount_max"]) == ("1825", "2425")


def test_ranges_with_thousand_separators_and_words():
    entry = one("2,000-9,000")

    assert (entry["amount_min_minor"], entry["amount_max_minor"]) == (200000, 900000)
    assert (entry["currency"], entry["currency_basis"]) == ("INR", "country_default")
    assert one("900 to 2500")["amount_max_minor"] == 250000
    assert one("Rs 500–800")["amount_min_minor"] == 50000


def test_a_reversed_range_is_not_read():
    result = price("₹500-200")

    assert (result.status, result.entries, result.reason) == ("unparsed", [], "reversed_range")


def test_from_is_a_qualifier():
    assert one("from ₹2,800")["qualifier"] == "from"
    assert one("From ₹2,800")["amount_minor"] == 280000
    assert one("about ₹100")["qualifier"] == "approx"
    assert one("₹100")["qualifier"] is None


@pytest.mark.parametrize(
    ("raw", "minor"),
    [("₹1,600", 160000), ("₹1,00,000", 10000000), ("₹12,34,567", 123456700), ("₹10.50", 1050)],
)
def test_valid_number_groupings(raw, minor):
    assert one(raw)["amount_minor"] == minor


@pytest.mark.parametrize("raw", ["₹1.600", "₹2,5", "₹1,60", "₹1.234,50", "₹1,2345"])
def test_ambiguous_numbers_are_left_unparsed(raw):
    result = price(raw)

    assert (result.status, result.entries, result.reason) == ("unparsed", [], "ambiguous_number")


def test_one_good_amount_and_one_ambiguous_number_is_partial():
    result = price("Adult ₹100, child ₹1.500")

    assert result.status == "partial"
    assert [e["audience"] for e in result.entries] == ["adult"]


def test_a_bare_number_takes_the_countrys_currency_and_says_so():
    entry = one("1800")

    assert (entry["amount_minor"], entry["currency"], entry["currency_basis"]) == (
        180000,
        "INR",
        "country_default",
    )


def test_a_bare_number_in_a_country_without_a_known_currency_has_no_currency():
    result = price("1800", country="FR")
    entry = result.entries[0]

    assert (entry["currency"], entry["currency_basis"], entry["amount_minor"]) == (
        None,
        "none",
        None,
    )
    assert (entry["amount"], result.status) == ("1800", "partial")


def test_exponents_come_from_the_currency_not_from_two():
    assert one("¥1000", "JP")["amount_minor"] == 1000
    assert one("KWD 2.5", "KW")["amount_minor"] == 2500
    assert one("€10", "DE")["amount_minor"] == 1000


def test_a_fraction_that_does_not_fit_the_exponent_is_not_converted():
    entry = one("¥10.5", "JP")

    assert (entry["amount"], entry["amount_minor"]) == ("10.5", None)
    assert price("¥10.5", "JP").status == "partial"


def test_a_currency_without_an_iso_exponent_is_not_converted_to_minor_units():
    entry = one("KPW 100", "KP")

    assert (entry["currency"], entry["amount"], entry["amount_minor"]) == ("KPW", "100", None)


def test_codes_and_unambiguous_symbols_of_other_currencies_are_read_anywhere():
    assert one("USD 15")["currency"] == "USD"
    assert one("€10")["currency"] == "EUR"
    assert one("US$10")["currency"] == "USD"
    assert one("USD 15")["amount_minor"] == 1500


def test_a_shared_symbol_is_read_only_when_it_is_the_countrys_currency():
    assert one("$10", "US")["currency"] == "USD"
    assert one("¥1000", "JP")["currency"] == "JPY"
    assert one("Rs 5", "IN")["currency"] == "INR"

    for raw, country in (("$10", "IN"), ("¥1000", "IN"), ("Rs 5", "JP")):
        entry = one(raw, country)
        assert (entry["currency"], entry["currency_ambiguous"], entry["amount_minor"]) == (
            None,
            True,
            None,
        ), raw
        assert price(raw, country).status == "partial"


def test_an_unrecognised_three_letter_code_is_ambiguous_not_the_default_currency():
    entry = one("XYZ 10")

    assert (entry["currency"], entry["currency_ambiguous"]) == (None, True)


def test_two_different_currencies_on_one_amount_are_ambiguous():
    entry = one("₹10-€20")

    assert (entry["currency"], entry["currency_ambiguous"]) == (None, True)


@pytest.mark.parametrize("raw", ["Free", "free", "No entry fees", "Free entry", "No charge"])
def test_explicit_free_wording(raw):
    result = price(raw)

    assert (result.status, result.is_free, result.entries) == ("label_only", True, [])


@pytest.mark.parametrize(
    ("raw", "tier"), [("Budget", "budget"), ("Mid-range", "mid-range"), ("Splurge", "splurge")]
)
def test_tier_words(raw, tier):
    result = price(raw)

    assert (result.status, result.tier, result.is_free) == ("label_only", tier, False)


def test_other_words_without_an_amount_are_unparsed():
    result = price("Varies")

    assert (result.status, result.reason, result.is_free) == ("unparsed", "no_amount", False)


def test_free_for_one_group_and_a_price_for_another():
    result = price("Free for children, ₹50 adults")

    assert [(e["audience"], e["amount_minor"], e["free"]) for e in result.entries] == [
        ("child", 0, True),
        ("adult", 5000, False),
    ]
    assert result.is_free is False  # not free for everyone


def test_numbers_that_are_not_prices_make_the_result_partial():
    result = price("₹50 plus 3 extra gates")

    assert result.status == "partial"
    assert result.reason == "leftover_digits"
    assert [e["amount_minor"] for e in result.entries] == [5000]


def test_dates_and_years_are_not_amounts():
    assert price("₹50 (2019)").status == "parsed"
    assert price("₹100 in 2021").status == "parsed"
    assert price("₹100 as of 2020-03-04").status == "parsed"
    assert [e["amount_minor"] for e in price("₹100 as of March 2020").entries] == [10000]


def test_a_label_between_two_amounts_with_no_separator_is_not_guessed():
    result = price("₹10 Child ₹5")

    assert result.status == "partial"
    assert [e["audience"] for e in result.entries] == [None, None]


def test_text_without_digits_or_known_words_is_unparsed():
    result = price("ask at the gate")

    assert (result.status, result.entries) == ("unparsed", [])


def test_masking_one_phrase_does_not_let_a_later_pattern_reach_across_it():
    result = price("₹125 for 5 kg ice")

    assert result.status == "parsed"
    assert (result.entries[0]["amount_minor"], result.entries[0]["unit"]) == (12500, "for 5")


def test_a_bracketed_label_right_after_an_amount_belongs_to_that_amount():
    result = price("₹10 (Indians) ₹200 (Foreigners)")

    assert result.status == "parsed"
    assert [(e["audience"], e["amount_minor"]) for e in result.entries] == [
        ("resident", 1000),
        ("foreigner", 20000),
    ]


def test_a_full_stop_between_sentences_separates_labels():
    result = price("Free Entry for Indians. For foreign tourist charged ₹50")

    assert result.status == "parsed"
    assert [(e["audience"], e["free"], e["amount_minor"]) for e in result.entries] == [
        ("resident", True, 0),
        ("foreigner", False, 5000),
    ]


def test_a_shared_symbol_in_a_range_follows_the_named_currency_in_the_same_range():
    entry = one("US$40 - $100")

    assert (entry["currency"], entry["amount_min_minor"], entry["amount_max_minor"]) == (
        "USD",
        4000,
        10000,
    )


def test_between_x_and_y_is_a_range_not_two_prices():
    entry = one("Ranging between ₹100 and ₹500 per person")

    assert (entry["amount_min_minor"], entry["amount_max_minor"]) == (10000, 50000)
    assert entry["unit"] == "per person"


def test_and_without_between_still_separates_two_prices():
    assert len(price("Adult ₹100 and child ₹50").entries) == 2
