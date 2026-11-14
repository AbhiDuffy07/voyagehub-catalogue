import random

from catalogue.blocks.areas import build_areas
from catalogue.blocks.rules import load_block_rules
from tests.blocksfixture import hours, stop

RULES = load_block_rules()


def names(areas):
    return [[s.name for s in a.members] for a in areas]


def test_stops_within_the_walk_radius_share_an_area():
    areas = build_areas([stop("A"), stop("B", km_north=0.5), stop("C", km_east=0.4)], RULES)
    assert len(areas) == 1 and len(areas[0].members) == 3


def test_a_stop_beyond_the_radius_starts_its_own_area():
    assert len(build_areas([stop("A"), stop("B", km_north=2.0)], RULES)) == 2


def test_the_radius_is_walking_distance_not_crow_flies():
    # 1.0 km straight = 1.3 km on foot, over 1.2; 0.8 km straight = 1.04 km on foot
    # (members join by distance from the area centre, so the second stop sits 0.5 km away from it)
    assert len(build_areas([stop("A"), stop("B", km_north=1.4)], RULES)) == 2
    assert len(build_areas([stop("A"), stop("B", km_north=0.8)], RULES)) == 1


def test_priority_stop_anchors_the_first_area():
    areas = build_areas([stop("Plain"), stop("Famous", km_north=5, unesco=True)], RULES)
    assert areas[0].anchor.name == "Famous"


def test_priority_order_unesco_photo_hours_kind_then_name():
    candidates = [
        stop("Z kind", kind="museum"),
        stop("Y hours", hours=hours(("09:00", "17:00"))),
        stop("X photo", has_photo=True),
        stop("W unesco", unesco=True),
        stop("A none"),
    ]
    order = [s.name for s in build_areas(candidates, RULES)[0].members]
    assert order == ["W unesco", "X photo", "Y hours", "Z kind", "A none"]


def test_result_does_not_depend_on_input_order():
    pts = [stop(f"S{i}", km_north=i * 0.7, km_east=(i % 3) * 0.8) for i in range(12)]
    first = names(build_areas(pts, RULES))
    for seed in range(5):
        shuffled = pts[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - reproducible, not security
        assert names(build_areas(shuffled, RULES)) == first


def test_area_centre_is_the_mean_of_its_members():
    area = build_areas([stop("A"), stop("B", km_north=0.8)], RULES)[0]
    assert abs(area.lat - (area.members[0].lat + area.members[1].lat) / 2) < 1e-9


def test_area_numbers_count_up_from_one():
    areas = build_areas([stop("A"), stop("B", km_north=5)], RULES)
    assert [a.number for a in areas] == [1, 2]
