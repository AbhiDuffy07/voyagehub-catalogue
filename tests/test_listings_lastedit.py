from datetime import date

import pytest

from catalogue.listings.lastedit import age_class, parse_lastedit

DUMP = date(2026, 11, 1)


@pytest.mark.parametrize(
    ("raw", "text", "precision", "year"),
    [
        ("2021-03-04", "2021-03-04", "day", 2021),
        (" 2021-03 ", "2021-03", "month", 2021),
        ("2019", "2019", "year", 2019),
        ("2026-11-01", "2026-11-01", "day", 2026),  # the dump day itself is fine
    ],
)
def test_well_formed_values_are_parsed_with_their_precision(raw, text, precision, year):
    result = parse_lastedit(raw, dump_date=DUMP)

    assert (result.status, result.date, result.precision, result.year) == (
        "parsed",
        text,
        precision,
        year,
    )


def test_an_empty_value_is_missing():
    result = parse_lastedit("", dump_date=DUMP)

    assert (result.status, result.date, result.precision, result.year) == (
        "missing",
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "raw", ["Octo", "14-0", "201-", "2021-13-01", "2021-02-30", "March 2020", "2021/03/04"]
)
def test_garbage_and_impossible_dates_stay_unparsed(raw):
    result = parse_lastedit(raw, dump_date=DUMP)

    assert (result.status, result.date, result.year) == ("unparsed", None, None)


@pytest.mark.parametrize("raw", ["2002-12-31", "1999", "2026-11-02", "2026-12", "2027"])
def test_years_before_wikivoyage_or_after_the_dump_are_implausible(raw):
    result = parse_lastedit(raw, dump_date=DUMP)

    assert (result.status, result.date, result.year) == ("implausible", None, None)


def test_the_month_of_the_dump_is_plausible():
    assert parse_lastedit("2026-11", dump_date=DUMP).status == "parsed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-06-15", "within"),
        ("2024-11-01", "within"),  # exactly two years before the dump
        ("2024-10-31", "older"),
        ("2020", "older"),
        ("2026", "within"),
        ("2025", "within"),
        ("2024", "unclear"),  # the year straddles the cutoff
        ("2024-10", "older"),
        ("2024-11", "within"),
        ("", "undated"),
        ("Octo", "undated"),
        ("2030", "undated"),
    ],
)
def test_age_class_against_the_two_year_cutoff(raw, expected):
    assert age_class(parse_lastedit(raw, dump_date=DUMP), dump_date=DUMP) == expected
