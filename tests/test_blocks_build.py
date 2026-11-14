import pytest

from catalogue.blocks.areas import build_areas
from catalogue.blocks.build import days_fillable, draft_blocks, is_open_in
from catalogue.blocks.rules import load_block_rules
from tests.blocksfixture import hours, stop

RULES = load_block_rules()


def draft(stops):
    areas = build_areas(stops, RULES)
    return draft_blocks(stops, areas, RULES)


def close(*names, **kw):
    """Stops a few hundred metres apart."""
    return [stop(n, km_north=i * 0.2, **kw) for i, n in enumerate(names)]


def test_three_close_stops_make_one_block():
    result = draft(close("A", "B", "C"))
    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert [s.stop.name for s in block.stops] == ["A", "B", "C"][: len(block.stops)]
    assert 2 <= len(block.stops) <= 4 and not result.unblocked


def test_a_lone_stop_is_reported_never_given_a_block():
    result = draft([stop("Alone")])
    assert result.blocks == []
    assert [(s.name, why) for s, why in result.unblocked] == [("Alone", "isolated_stop")]


def test_at_most_four_stops_and_the_rest_left_over():
    result = draft(close("A", "B", "C", "D", "E", kind="religious"))
    assert [len(b.stops) for b in result.blocks][0] == 4
    assert len(result.unblocked) == 1  # one stop cannot make a block


def test_a_long_visit_limits_how_many_stops_fit_a_half_day():
    forts = close("F1", "F2", "F3", "F4", kind="fort_palace")  # 105 min each at the midpoint
    result = draft(forts)
    assert len(result.blocks[0].stops) == 2
    assert result.blocks[0].minutes <= RULES.blocks.half_day_minutes


def test_every_stop_is_in_at_most_one_block():
    stops = close(*[f"S{i}" for i in range(9)], kind="religious")
    result = draft(stops)
    used = [s.stop.key for b in result.blocks for s in b.stops]
    assert len(used) == len(set(used))
    assert len(used) + len(result.unblocked) == len(stops)


def test_a_stop_closed_for_the_slot_is_left_out_of_that_block():
    early = stop("Early", km_north=0.1, kind="religious", hours=hours(("05:00", "07:00")))
    result = draft([*close("A", "B", kind="religious"), early])
    block = result.blocks[0]
    assert block.slot == "morning"
    assert "Early" not in [s.stop.name for s in block.stops]
    assert ("Early", "closed_in_slot") in [(s.name, why) for s, why in result.unblocked]


def test_unknown_hours_never_exclude_a_stop_and_are_counted():
    open_stop = stop("Open", kind="religious", hours=hours(("08:00", "18:00")))
    result = draft([open_stop, *close("A", "B", kind="religious", km_east=0.1)])
    block = result.blocks[0]
    assert len(block.stops) == 3 and block.hours_unknown_count == 2


def test_slot_comes_from_the_first_stops_kind():
    assert draft(close("Fort", "Gate", kind="fort_palace")).blocks[0].slot == "morning"
    assert draft(close("Hall", "Wing", kind="museum")).blocks[0].slot == "afternoon"


def test_two_blocks_of_unknown_kind_in_one_area_alternate_slots():
    # eight stops of unknown kind in a tight cluster: two blocks, morning then afternoon
    stops = [stop(f"S{i}", km_north=(i % 4) * 0.1, km_east=(i // 4) * 0.1) for i in range(8)]
    slots = [b.slot for b in draft(stops).blocks]
    assert slots[:2] == ["morning", "afternoon"]


def test_a_far_area_is_a_day_trip_with_a_road_transfer_estimate():
    town = [stop(f"T{i}", km_north=i * 0.2) for i in range(6)]
    amber = [stop(f"Amber{i}", km_north=11 + i * 0.2, kind="fort_palace") for i in range(3)]
    result = draft([*town, *amber])
    trips = [b for b in result.blocks if b.day_trip]
    assert len(trips) == 1 and trips[0].slot == "full_day"
    assert trips[0].transfer_minutes > 20 and trips[0].transfer_method == "estimate"
    assert all(not b.day_trip for b in result.blocks if b is not trips[0])


def test_a_short_far_block_is_a_half_day_not_a_full_day():
    town = [stop(f"T{i}", km_north=i * 0.2) for i in range(6)]
    far = [stop("Far A", km_north=11, kind="religious"), stop("Far B", km_north=11.2)]
    trip = next(b for b in draft([*town, *far]).blocks if b.day_trip)
    assert trip.slot == "morning" and trip.transfer_minutes > 20
    assert trip.minutes + 2 * trip.transfer_minutes <= RULES.blocks.half_day_minutes


def test_neighbouring_far_areas_share_one_day_trip():
    town = [stop(f"T{i}", km_north=(i % 4) * 0.1, km_east=(i // 4) * 0.1) for i in range(8)]
    zone = [stop(f"Z{i}", km_north=11 + i * 0.2, kind="fort_palace") for i in range(3)]
    zone += [stop(f"Y{i}", km_north=12.8 + i * 0.2, kind="fort_palace") for i in range(3)]
    result = draft([*town, *zone])
    assert len([b for b in result.blocks if b.day_trip]) == 1
    assert {why for _, why in result.unblocked} == {"day_trip_area_limit"}


def test_an_area_gets_at_most_two_blocks_one_per_half_day():
    stops = [stop(f"S{i}", km_north=(i % 4) * 0.1, km_east=(i // 4) * 0.1) for i in range(12)]
    result = draft(stops)
    assert [b.slot for b in result.blocks] == ["morning", "afternoon"]
    assert [why for _, why in result.unblocked] == ["area_block_limit"] * 4


def test_two_isolated_stops_close_enough_are_paired_with_a_road_hop():
    result = draft([stop("A"), stop("B", km_north=2.0)])
    assert len(result.blocks) == 1
    assert any(s.hop_method == "road" for s in result.blocks[0].stops)


def test_two_isolated_stops_too_far_apart_stay_isolated():
    result = draft([stop("A"), stop("B", km_north=8.0)])
    assert result.blocks == [] and len(result.unblocked) == 2


def test_a_photo_stop_anchors_a_block_over_a_plain_one():
    result = draft([stop("Plain"), stop("Pic", km_north=0.2, has_photo=True), stop("Z")])
    assert result.blocks[0].stops[0].stop.name == "Pic"
    assert result.blocks[0].photo_count == 1


def test_no_walking_hop_inside_an_area_block_exceeds_the_limit():
    stops = [stop(f"S{i}", km_north=(i % 5) * 0.4, km_east=(i // 5) * 0.4) for i in range(15)]
    for block in draft(stops).blocks:
        walks = [s.hop_minutes for s in block.stops if s.hop_method == "walk"]
        assert all(w <= RULES.travel.max_walk_hop_min for w in walks)


def test_stop_minutes_and_default_basis_are_recorded():
    block = draft(close("A", "B")).blocks[0]
    first = block.stops[0]
    assert (first.minutes_min, first.minutes_max, first.duration_basis) == (30, 60, "default")
    typed = draft(close("A", "B", kind="museum")).blocks[0].stops[0]
    assert (typed.minutes_min, typed.minutes_max, typed.duration_basis) == (60, 90, "kind")


def test_text_is_facts_only_and_labels_estimates():
    block = draft(close("Amer Fort", "Panna Meena", kind="religious")).blocks[0]
    assert block.title == "Morning around Amer Fort"
    assert "Amer Fort" in block.intro and "Panna Meena" in block.intro
    assert "estimate" in block.intro
    for banned in ("₹", "http", "best", "must", "cheapest", "guaranteed"):
        assert banned not in block.title + block.intro


def _block(slot, basis):
    from catalogue.blocks.model import Block

    return Block(1, "x", [1], slot, False, [], 0, 0.0, 0, 0, slot_basis=basis)


@pytest.mark.parametrize(
    "slots,expected",
    [
        (["morning", "morning"], 0),  # two mornings never make a day
        (["morning", "afternoon"], 1),
        (["morning", "morning", "afternoon"], 1),
        (["full_day", "morning", "afternoon", "full_day"], 3),
        (["alternate", "alternate"], 1),  # flexible blocks fill either half
        (["morning", "morning", "alternate", "alternate"], 2),
        ([], 0),
    ],
)
def test_days_fillable_needs_a_morning_and_an_afternoon_or_a_full_day(slots, expected):
    blocks = [
        _block("full_day", "trip")
        if s == "full_day"
        else _block("morning", "alternate")
        if s == "alternate"
        else _block(s, "kind")
        for s in slots
    ]
    assert days_fillable(blocks) == expected


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_stops_gives_no_blocks(count):
    result = draft([stop(f"S{i}", km_north=i) for i in range(count)])
    assert result.blocks == []


# --- hours, found in review ---------------------------------------------------------------


def test_a_closed_stop_that_is_not_the_anchor_is_filtered_out_of_the_block():
    anchor = stop("Anchor", kind="religious", has_photo=True)
    closed = stop("Closed", km_north=0.1, kind="religious", hours=hours(("05:00", "07:00")))
    fine = stop("Fine", km_north=0.2, kind="religious")
    block = draft([anchor, closed, fine]).blocks[0]
    assert [s.stop.name for s in block.stops] == ["Anchor", "Fine"]


def test_a_far_block_never_holds_a_stop_closed_for_its_half_day_slot():
    town = [stop(f"T{i}", km_north=(i % 4) * 0.1, km_east=(i // 4) * 0.1) for i in range(8)]
    afternoon_only = stop("Late", km_north=11.0, kind="religious", hours=hours(("14:00", "17:00")))
    friend = stop("Friend", km_north=11.2, kind="religious", has_photo=True)
    far_blocks = [b for b in draft([*town, afternoon_only, friend]).blocks if b.day_trip]
    for block in far_blocks:
        window = RULES.windows["full_day" if block.slot == "full_day" else block.slot]
        assert all(is_open_in(s.stop.hours, window) for s in block.stops)
    assert not any(
        b.slot == "morning" for b in far_blocks if "Late" in [s.stop.name for s in b.stops]
    )


def test_an_anchor_flipped_by_its_hours_is_not_flexible():
    afternoon_only = hours(("14:00", "17:00"))
    stops = [stop(f"S{i}", km_north=i * 0.1, hours=afternoon_only) for i in range(2)]
    block = draft(stops).blocks[0]
    assert block.slot == "afternoon" and block.slot_basis == "hours"
    again = draft([stop(f"Z{i}", km_north=i * 0.1, hours=afternoon_only) for i in range(2)])
    assert days_fillable([block, again.blocks[0]]) == 0  # two afternoons never make a day


def test_a_flexible_block_must_be_open_in_both_halves():
    stops = [stop("A", hours=hours(("14:00", "17:00"))), stop("B", km_north=0.1)]
    block = draft(stops).blocks[0]
    assert block.slot_basis != "alternate"


def test_a_stop_too_far_for_a_day_trip_has_its_own_reason():
    town = [stop(f"T{i}", km_north=(i % 4) * 0.1, km_east=(i // 4) * 0.1) for i in range(8)]
    remote = [stop("Remote A", km_north=70.0), stop("Remote B", km_north=70.2)]
    result = draft([*town, *remote])
    assert {why for _, why in result.unblocked} == {"too_far_for_day_trip"}


def test_paired_stops_stay_within_the_radius_of_the_first_stop():
    # a chain of three stops 5 km apart would run 10 km; only the first pair is within 6 km
    stops = [stop("A"), stop("B", km_north=5.0), stop("C", km_north=10.0)]
    result = draft(stops)
    assert [len(b.stops) for b in result.blocks] == [2]
    assert [why for _, why in result.unblocked] == ["isolated_stop"]


@pytest.mark.parametrize(
    "parsed,window,expected",
    [
        (None, (540, 780), True),
        ({"always_open": True}, (540, 780), True),
        (hours(("22:00", "02:00")), (540, 780), False),  # overnight stays out of the day
        (hours(("22:00", "02:00")), (0, 60), True),
        (hours(("09:00", "17:00")), (780, 1020), True),
        (hours(("05:00", "07:00")), (540, 780), False),
        (hours(("05:00", "09:00")), (540, 780), False),  # closes exactly as the slot opens
        (hours(("00:00", "24:00")), (540, 780), True),
        ({"schedule": [{"days": ["mon"]}]}, (540, 780), False),  # no ranges: closed for the slot
    ],
)
def test_is_open_in_cases(parsed, window, expected):
    assert is_open_in(parsed, window) is expected


# --- UNESCO sites take longer (B10) ---------------------------------------------------------


def test_a_unesco_stop_has_the_longer_estimate_and_says_so():
    stops = [stop("Taj", unesco=True, kind="sight"), stop("Next", km_north=0.2)]
    first = draft(stops).blocks[0].stops[0]
    assert first.duration_basis == "unesco"
    assert (first.minutes_min, first.minutes_max) == RULES.unesco_duration


def test_a_long_unesco_visit_limits_how_many_stops_fit_the_half_day():
    stops = [stop("Taj", unesco=True)] + [stop(f"S{i}", km_north=0.1 * (i + 1)) for i in range(3)]
    block = draft(stops).blocks[0]
    assert block.stops[0].stop.name == "Taj" and len(block.stops) < 4
    assert block.minutes <= RULES.blocks.half_day_minutes
