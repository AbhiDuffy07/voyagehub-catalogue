from datetime import date
from types import SimpleNamespace

import pytest

from catalogue.blocks.build import days_fillable, pair_days
from catalogue.prices.costs import day_cost, estimate_block
from catalogue.prices.pricing import build_bands, classify_listing, load_price_rules
from tests.test_prices_pricing import entry, listing

RULES = load_price_rules()
DUMP = date(2026, 11, 1)


def view(name, slot, basis="kind"):
    return SimpleNamespace(name=name, slot=slot, slot_basis=basis)


def test_pair_days_pairs_a_morning_with_an_afternoon_and_keeps_a_full_day_alone():
    blocks = [view("m1", "morning"), view("a1", "afternoon"), view("f", "full_day")]
    days = pair_days(blocks)
    assert sorted(sorted(b.name for b in day) for day in days) == [["a1", "m1"], ["f"]]


def test_pair_days_lets_a_flexible_block_fill_the_spare_half():
    blocks = [view("m1", "morning"), view("m2", "morning"), view("x", "morning", "alternate")]
    days = pair_days(blocks)
    assert [[b.name for b in day] for day in days] == [["m1", "x"]]


@pytest.mark.parametrize(
    "slots",
    [
        [("morning", "kind")] * 3 + [("afternoon", "kind")],
        [("morning", "alternate")] * 5,
        [("full_day", "kind"), ("afternoon", "alternate"), ("morning", "kind")],
        [],
    ],
)
def test_days_fillable_is_the_number_of_paired_days(slots):
    blocks = [view(str(i), slot, basis) for i, (slot, basis) in enumerate(slots)]
    assert days_fillable(blocks) == len(pair_days(blocks))


def test_no_block_is_used_in_two_days():
    blocks = [view(str(i), "morning", "alternate") for i in range(7)]
    used = [b.name for day in pair_days(blocks) for b in day]
    assert len(used) == len(set(used))


def fresh(entries, *, kind="see", page=1, ordinal=1, city=7, **kw):
    return classify_listing(
        listing(entries, kind=kind, page_id=page, ordinal=ordinal, owner_page_id=city, **kw),
        DUMP,
        RULES,
    )


def stop_row(page=1, ordinal=1, *, source="listing", kind="sight"):
    return {"page_id": page, "ordinal": ordinal, "source": source, "kind": kind, "name": "S"}


def block(*, slot="morning", city=7, block_id="b1"):
    return {"block_id": block_id, "owner_page_id": city, "slot": slot}


def estimate(stops, prices, bands=None, *, slot="morning"):
    index = {(p.page_id, p.ordinal): p for p in prices}
    return estimate_block(block(slot=slot), stops, index, bands or {}, "INR", RULES)


def test_an_exact_tier_price_is_used_per_visitor_group():
    prices = [fresh([entry(50, audience="resident"), entry(300, audience="foreigner")])]
    result = estimate([stop_row()], prices)
    assert result["label"] == "estimate"
    assert result["resident"]["exact"] == {"low_minor": 5000, "high_minor": 5000, "stops": 1}
    assert result["foreigner"]["exact"] == {"low_minor": 30000, "high_minor": 30000, "stops": 1}
    assert result["foreigner"]["total_low_minor"] == 30000


def test_an_untiered_price_applies_to_both_groups():
    result = estimate([stop_row()], [fresh([entry(100)])])
    assert result["resident"]["exact"]["low_minor"] == result["foreigner"]["exact"]["low_minor"]


def test_only_the_other_groups_tier_is_unknown_for_this_group():
    result = estimate([stop_row()], [fresh([entry(300, audience="foreigner")])])
    assert result["foreigner"]["exact"]["stops"] == 1
    assert result["resident"]["exact"]["stops"] == 0 and result["resident"]["unknown_stops"] == 1
    assert result["resident"]["total_low_minor"] is None


def test_an_old_price_is_never_exact_but_the_city_band_stands_in_as_approximate():
    old = fresh([entry(500)], edit="2019-01-01")
    others = [
        fresh([entry(v)], ordinal=i + 2, edit="2019-01-01") for i, v in enumerate((100, 200, 300))
    ]
    bands = build_bands([old, *others], RULES)
    result = estimate([stop_row()], [old], bands)["foreigner"]
    assert result["exact"]["stops"] == 0
    assert result["approx"]["stops"] == 1
    assert (result["approx"]["low_minor"], result["approx"]["high_minor"]) == (
        bands[(7, "see", "INR", "foreigner")]["low_minor"],
        bands[(7, "see", "INR", "foreigner")]["high_minor"],
    )
    assert "city band" in result["basis"]


def test_a_stop_with_no_price_and_no_band_is_unknown_never_zero():
    result = estimate([stop_row()], [])["foreigner"]
    assert result["unknown_stops"] == 1
    assert result["total_low_minor"] is None and result["total_high_minor"] is None
    assert result["note"] == "Check on official site"


def test_a_stop_from_an_article_is_unknown_not_banded():
    bands = build_bands([fresh([entry(i)], ordinal=i) for i in (1, 2, 3)], RULES)
    result = estimate([stop_row(source="article")], [], bands)["foreigner"]
    assert result["unknown_stops"] == 1 and result["approx"]["stops"] == 0


def test_a_foreign_currency_price_is_not_summed_into_the_home_currency():
    usd = fresh([entry(5, currency="USD")])
    result = estimate([stop_row()], [usd])["foreigner"]
    assert result["exact"]["stops"] == 0 and result["unknown_stops"] == 1


def test_food_comes_from_the_eat_band_one_meal_per_half_day_two_per_full_day():
    eat = [fresh([entry(v)], kind="eat", page=2, ordinal=i) for i, v in enumerate((100, 200, 300))]
    bands = build_bands(eat, RULES)
    band = bands[(7, "eat", "INR", "foreigner")]
    half = estimate([stop_row()], [], bands)["foreigner"]["food"]
    assert half == {
        "low_minor": band["low_minor"],
        "high_minor": band["high_minor"],
        "meals": 1,
        "n_prices": 3,
    }
    full = estimate([stop_row()], [], bands, slot="full_day")["foreigner"]["food"]
    assert full["meals"] == 2 and full["low_minor"] == 2 * band["low_minor"]


def test_no_eat_band_means_no_food_figure():
    assert estimate([stop_row()], [fresh([entry(10)])])["foreigner"]["food"] is None


def test_the_total_adds_exact_approximate_and_food_and_names_the_missing_transport():
    stops = [stop_row(1, 1), stop_row(1, 2)]
    exact = fresh([entry(100)], ordinal=1)
    bands = build_bands(
        [fresh([entry(v)], ordinal=10 + v, edit="2019-01-01") for v in (10, 20, 30)]
        + [fresh([entry(v)], kind="eat", page=2, ordinal=v) for v in (100, 200, 300)],
        RULES,
    )
    unpriced = fresh([entry(500)], ordinal=2, edit="2019-01-01")
    result = estimate(stops, [exact, unpriced], bands)["foreigner"]
    low = (
        10000
        + bands[(7, "see", "INR", "foreigner")]["low_minor"]
        + bands[(7, "eat", "INR", "foreigner")]["low_minor"]
    )
    assert result["total_low_minor"] == low
    assert result["transport"] == "not included: no fare data"
    assert "transport not included" in result["basis"]
    assert result["currency"] == "INR"


def test_free_exact_stop_adds_zero():
    result = estimate([stop_row()], [fresh([entry(free=True)], free=True)])["foreigner"]
    assert result["exact"]["stops"] == 1 and result["total_low_minor"] == 0


def test_a_day_adds_its_blocks_and_stays_unknown_when_every_block_is_unknown():
    stops = [stop_row()]
    prices = [fresh([entry(100)])]
    a = estimate(stops, prices)
    b = estimate(stops, prices)
    day = day_cost([a, b])
    assert day["label"] == "estimate"
    assert day["foreigner"]["total_low_minor"] == 20000
    unknown = estimate(stops, [])
    assert day_cost([unknown, unknown])["foreigner"]["total_low_minor"] is None
    mixed = day_cost([a, unknown])["foreigner"]
    assert mixed["total_low_minor"] == 10000 and mixed["unknown_stops"] == 1


def test_each_stop_says_where_its_fee_came_from():
    old = [fresh([entry(v)], ordinal=i + 2, edit="2019-01-01") for i, v in enumerate((10, 20, 30))]
    bands = build_bands(old, RULES)
    prices = [fresh([entry(100)], ordinal=1), old[0]]
    stops = [stop_row(1, 1), stop_row(1, 2), stop_row(1, 99), stop_row(2, 1, source="article")]
    result = estimate(stops, prices, bands)["foreigner"]
    assert result["stop_basis"] == ["exact", "city_band", "unknown", "unknown"]


def test_a_total_with_an_unknown_stop_says_it_is_not_complete():
    free = fresh([entry(free=True)], free=True)
    partial = estimate([stop_row(1, 1), stop_row(1, 2)], [free])
    group = partial["foreigner"]
    assert group["total_low_minor"] == 0 and group["unknown_stops"] == 1
    assert group["complete"] is False
    whole = estimate([stop_row(1, 1)], [free])["foreigner"]
    assert whole["complete"] is True
    assert estimate([stop_row()], [])["foreigner"]["complete"] is False


def test_a_day_is_complete_only_when_every_block_is():
    free = fresh([entry(free=True)], free=True)
    done = estimate([stop_row(1, 1)], [free])
    half = estimate([stop_row(1, 1), stop_row(1, 2)], [free])
    assert day_cost([done, done])["foreigner"]["complete"] is True
    assert day_cost([done, half])["foreigner"]["complete"] is False


def test_a_stop_whose_listing_has_no_price_at_all_is_unknown_not_banded():
    old = [fresh([entry(v)], ordinal=i + 2, edit="2019-01-01") for i, v in enumerate((10, 20, 30))]
    bands = build_bands(old, RULES)
    unpriced = classify_listing(
        listing([], status="empty", page_id=1, ordinal=1, owner_page_id=7), DUMP, RULES
    )
    result = estimate([stop_row(1, 1)], [unpriced], bands)["foreigner"]
    assert result["stop_basis"] == ["unknown"] and result["approx"]["stops"] == 0


def test_a_free_exact_stop_counts_even_when_the_citys_currency_is_unknown():
    free = fresh([entry(free=True)], free=True)
    index = {(p.page_id, p.ordinal): p for p in [free]}
    result = estimate_block(block(), [stop_row()], index, {}, None, RULES)["foreigner"]
    assert result["exact"]["stops"] == 1 and result["total_low_minor"] == 0
    assert result["complete"] is True
