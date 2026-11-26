"""World mode: prices work in any currency (minor units, no exchange rates) and ride apps are
India-only (null elsewhere)."""

from datetime import date

import pytest

from catalogue.prices.costs import estimate_block
from catalogue.prices.fees import OfficialFee
from catalogue.prices.pricing import build_bands, classify_listing, load_price_rules
from catalogue.prices.run import _apply_fees
from catalogue.publish import build as b
from tests.test_prices_pricing import DUMP, entry, listing

RULES = load_price_rules()
CITY_ID = 7

# country, currency, exponent, amounts in whole units, the median in minor units
CASES = [
    ("US", "USD", 2, [10, 20, 30], 2000),
    ("GB", "GBP", 2, [12.5, 15, 17.5], 1500),
    ("DE", "EUR", 2, [8, 9, 11], 900),
    ("JP", "JPY", 0, [500, 1000, 1500], 1000),  # no minor units: 1,000 yen is 1000
]


def prices_in(currency, exponent, amounts, kind="see", first=1):
    out = []
    for i, amount in enumerate(amounts):
        minor = int(round(amount * 10**exponent))
        row = listing(
            [entry(amount, currency=currency, amount_minor=minor, audience="adult")],
            kind=kind,
            ordinal=first + i,
            owner_page_id=CITY_ID,
        )
        out.append(classify_listing(row, DUMP, RULES))
    return out


@pytest.mark.parametrize(("country", "currency", "exponent", "amounts", "median"), CASES)
def test_a_band_is_built_in_the_cities_own_currency_and_minor_units(
    country, currency, exponent, amounts, median
):
    prices = prices_in(currency, exponent, amounts)

    bands = build_bands(prices, RULES)

    band = bands[(CITY_ID, "see", currency, "foreigner")]
    assert band["median_minor"] == median
    assert band["low_minor"] <= median <= band["high_minor"]
    assert band["label"] == "approximate" and band["n_prices"] == 3
    assert not any(key[2] == "INR" for key in bands)  # nothing is converted to rupees


def test_bands_of_two_currencies_in_one_city_stay_apart_with_no_conversion():
    prices = prices_in("USD", 2, [10, 20, 30]) + prices_in("EUR", 2, [8, 9, 11], first=10)

    bands = build_bands(prices, RULES)

    assert bands[(CITY_ID, "see", "USD", "foreigner")]["median_minor"] == 2000
    assert bands[(CITY_ID, "see", "EUR", "foreigner")]["median_minor"] == 900


@pytest.mark.parametrize(("country", "currency", "exponent", "amounts", "median"), CASES)
def test_a_block_cost_is_summed_in_the_cities_currency_without_scaling(
    country, currency, exponent, amounts, median
):
    prices = prices_in(currency, exponent, amounts)
    index = {(p.page_id, p.ordinal): p for p in prices}
    stop = {"page_id": 1, "ordinal": 1, "source": "listing", "kind": "sight", "name": "S"}
    block = {"block_id": "b1", "owner_page_id": CITY_ID, "slot": "morning"}

    result = estimate_block(block, [stop], index, {}, currency, RULES)["foreigner"]

    first_minor = int(round(amounts[0] * 10**exponent))
    assert result["exact"] == {"low_minor": first_minor, "high_minor": first_minor, "stops": 1}


@pytest.mark.parametrize(
    ("currency", "exponent", "amount", "minor"),
    [
        ("USD", 2, "25", 2500),
        ("EUR", 2, "12", 1200),
        ("GBP", 2, "20", 2000),
        ("JPY", 0, "1000", 1000),
    ],
)
def test_an_official_fee_becomes_minor_units_by_the_iso_exponent(currency, exponent, amount, minor):
    fee = OfficialFee(
        owner_slug="x",
        listing_key="see:1",
        place="P",
        currency=currency,
        resident=amount,
        foreigner=amount,
        source_url="https://example.org/fees",
        date_read=date(2026, 10, 1),
        note="",
    )
    row = {"owner_slug": "x", "listing_key": "see:1", "page_id": 1, "ordinal": 1}
    price = classify_listing(listing([], page_id=1, ordinal=1), DUMP, RULES)

    out, used, _ = _apply_fees([(row, price)], {("x", "see:1"): fee}, {currency: exponent}, DUMP)

    assert out[0][1].currency == currency
    assert out[0][1].tiers == {"resident": (minor, minor), "foreigner": (minor, minor)}


# --- the published band keeps currency and minor units ----------------------------------------


def _up(bands):
    return b.Upstream(
        place_listings={CITY_ID: []},
        photos={},
        summaries={},
        timezones={},
        blocks={},
        stops={},
        block_costs={},
        days={},
        bands={CITY_ID: bands},
    )


@pytest.mark.parametrize(("country", "currency", "exponent", "amounts", "median"), CASES)
def test_published_bands_carry_the_currency_code_and_minor_units(
    country, currency, exponent, amounts, median
):
    band = {
        "kind": "see",
        "group": "foreigner",
        "basis": "fresh",
        "currency": currency,
        "low_minor": median - 100,
        "high_minor": median + 100,
        "median_minor": median,
        "label": "approximate",
        "n_prices": 3,
        "n_fresh": 3,
    }
    row = {
        "wikivoyage_page_id": CITY_ID,
        "slug": "x",
        "title": "X",
        "country_iso": country,
        "tier": "browse_only",
    }

    city = b.build_city(row, _up([band]))

    assert [(x["currency"], x["median_minor"]) for x in city["price_bands"]] == [(currency, median)]


# --- ride apps: only where a table lists the city ---------------------------------------------


@pytest.mark.parametrize("country", ["US", "JP", "GB", "DE", None])
def test_ride_apps_are_null_outside_india(country):
    row = {
        "wikivoyage_page_id": CITY_ID,
        "slug": "x",
        "title": "X",
        "country_iso": country,
        "tier": "browse_only",
    }

    city = b.build_city(row, _up([]))

    assert city["getting_around"]["ride_apps"] is None


def test_ride_apps_are_present_for_an_indian_city_an_app_lists():
    row = {
        "wikivoyage_page_id": CITY_ID,
        "slug": "jaipur",
        "title": "X",
        "country_iso": "IN",
        "tier": "browse_only",
    }

    city = b.build_city(row, _up([]))

    assert city["getting_around"]["ride_apps"]["apps"]


def test_ride_apps_are_null_for_an_indian_city_no_app_lists():
    row = {
        "wikivoyage_page_id": CITY_ID,
        "slug": "x",
        "title": "X",
        "country_iso": "IN",
        "tier": "browse_only",
    }

    city = b.build_city(row, _up([]))

    assert city["getting_around"]["ride_apps"] is None
