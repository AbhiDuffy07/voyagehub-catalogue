from collections import Counter

from catalogue.nearby.order import city_order
from catalogue.readiness.rules import load_readiness_rules

RULES = load_readiness_rules()  # 3 photographed places, 1 hero photo


def test_cities_closest_to_ready_come_first():
    places = Counter({5: 2, 3: 0, 9: 2, 7: 2})
    hero = Counter({5: 1, 3: 1, 9: 1})  # 7 has no hero yet
    has_summary = {5, 3, 7}  # 9 has none, so it cannot become ready this run
    assert city_order([9, 7, 5, 3], places, hero, has_summary, RULES) == [5, 7, 3, 9]


def test_ties_break_on_page_id_and_the_order_is_stable():
    places, hero, summary = Counter({8: 1, 4: 1}), Counter({8: 1, 4: 1}), {8, 4}
    assert city_order([8, 4], places, hero, summary, RULES) == [4, 8]
    assert city_order([4, 8], places, hero, summary, RULES) == [4, 8]
