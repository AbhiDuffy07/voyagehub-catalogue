import pytest

from catalogue.blocks.geo import haversine_km, road_minutes, walk_minutes
from catalogue.blocks.rules import load_block_rules

RULES = load_block_rules()


def test_haversine_one_hundredth_degree_of_latitude():
    assert haversine_km(26.9, 75.8, 26.91, 75.8) == pytest.approx(1.112, abs=0.005)


def test_haversine_is_symmetric_and_zero_for_the_same_point():
    assert haversine_km(26.9, 75.8, 27.1, 78.0) == haversine_km(27.1, 78.0, 26.9, 75.8)
    assert haversine_km(26.9, 75.8, 26.9, 75.8) == 0


def test_walk_minutes_is_distance_times_detour_over_speed():
    assert walk_minutes(1.0, RULES.travel) == pytest.approx(1.0 * 1.3 / 4.5 * 60)


def test_road_minutes_uses_the_road_speed_assumption():
    assert road_minutes(10.0, RULES.travel) == pytest.approx(10.0 * 1.3 / 20.0 * 60)
