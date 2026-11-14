"""Draft day blocks (2-4 stops) from one city's areas. Facts and estimates only, never invented.

For each area, in order: the highest-priority unused stop anchors a block; its slot comes from its
kind (config/block_rules.toml), alternating morning/afternoon when the kind says nothing. The block
grows by the nearest unused stop (a short nearest-neighbour path) while it stays inside the slot's
time budget, a walking hop stays under the limit, and the stop is not closed for the slot by its own
parsed hours (unknown hours never exclude a stop). A far area is a day trip: one full-day block with
an estimated road transfer from the city's centre. Stops that cannot make a block of at least two
are first paired with the nearest other left-over stop within a stated radius (a road hop), and
otherwise reported, never given a one-stop block. Every stop is used at most once.
"""

import statistics
from dataclasses import dataclass, field

from catalogue.blocks.areas import priority_key
from catalogue.blocks.geo import haversine_km, road_minutes, walk_km, walk_minutes
from catalogue.blocks.model import Area, Block, BlockStop, Stop
from catalogue.blocks.rules import BlockRules

SLOT_LABELS = {"morning": "Morning", "afternoon": "Afternoon", "full_day": "Full day"}
HALF_DAY_SLOTS = ("morning", "afternoon")


@dataclass(slots=True)
class BlockResult:
    blocks: list[Block] = field(default_factory=list)
    unblocked: list[tuple[Stop, str]] = field(default_factory=list)


def is_open_in(hours: dict | None, window: tuple[int, int]) -> bool:
    """False only when parsed hours leave the stop closed for the whole window on every day.
    Unknown hours are never a reason to exclude."""
    if hours is None or hours.get("always_open"):
        return True
    start, end = window
    for entry in hours.get("schedule") or []:
        for opens, closes in entry.get("ranges") or []:
            try:
                lo, hi = _minutes(opens), _minutes(closes)
            except (ValueError, TypeError, AttributeError):
                continue  # a range that cannot be read says nothing
            if lo == hi:
                continue
            spans = [(lo, hi)] if hi > lo else [(lo, 1440), (0, hi)]
            if any(a < end and b > start for a, b in spans):
                return True
    return False


def _minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def _visit(stop: Stop, rules: BlockRules) -> tuple[int, int, str, int]:
    low, high, basis = rules.duration(stop.kind, stop.section)
    return low, high, basis, round((low + high) / 2)


def _hop(a: Stop, b: Stop, rules: BlockRules) -> tuple[float, float, str]:
    """(minutes, km, method): on foot when it is within the walking limit, else by road."""
    straight = haversine_km(a.lat, a.lon, b.lat, b.lon)
    minutes = walk_minutes(straight, rules.travel)
    if minutes <= rules.travel.max_walk_hop_min:
        return minutes, walk_km(straight, rules.travel), "walk"
    return road_minutes(straight, rules.travel), walk_km(straight, rules.travel), "road"


def _grow(
    anchor: Stop,
    pool: list[Stop],
    slot: str,
    cap: float,
    rules: BlockRules,
    *,
    only_hops_within_walk: bool,
    radius_km: float | None = None,
) -> list[tuple[Stop, float, float, str]] | None:
    """The block's stops in path order as (stop, hop minutes, hop km, hop method), or None when
    fewer than the minimum fit."""
    window = rules.windows[slot]
    path = [(anchor, 0.0, 0.0, "")]
    total = float(_visit(anchor, rules)[3])
    others = [s for s in pool if s is not anchor and is_open_in(s.hours, window)]
    last = anchor
    while len(path) < rules.blocks.max_stops and others:
        ranked = sorted(
            others,
            key=lambda s: (haversine_km(last.lat, last.lon, s.lat, s.lon), priority_key(s)),
        )
        pick = None
        for cand in ranked:
            minutes, km, method = _hop(last, cand, rules)
            if only_hops_within_walk and method != "walk":
                continue
            if (
                radius_km is not None
                and haversine_km(anchor.lat, anchor.lon, cand.lat, cand.lon) > radius_km
            ):
                continue
            if total + minutes + _visit(cand, rules)[3] > cap:
                continue
            pick = (cand, minutes, km, method)
            break
        if pick is None:
            break
        path.append(pick)
        total += pick[1] + _visit(pick[0], rules)[3]
        last = pick[0]
        others.remove(pick[0])
    return path if len(path) >= rules.blocks.min_stops else None


def _duration_text(minutes: float) -> str:
    return f"about {round(minutes / 30) / 2:g} h"


def _finish(
    owner: Stop,
    path: list[tuple[Stop, float, float, str]],
    slot: str,
    day_trip: bool,
    centre: tuple[float, float],
    area_of: dict[int, int],
    rules: BlockRules,
) -> Block:
    stops = []
    for position, (stop, minutes, km, method) in enumerate(path, start=1):
        low, high, basis, _ = _visit(stop, rules)
        stops.append(
            BlockStop(
                stop=stop,
                position=position,
                minutes_min=low,
                minutes_max=high,
                duration_basis=basis,
                hop_minutes=round(minutes, 1),
                hop_km=round(km, 3),
                hop_method=method,
                hours_known=stop.hours is not None,
            )
        )
    total = sum(_visit(s.stop, rules)[3] for s in stops) + sum(s.hop_minutes for s in stops)
    walk = sum(s.hop_km for s in stops if s.hop_method == "walk")
    block = Block(
        owner_id=owner.owner_id,
        owner_slug=owner.owner_slug,
        area_numbers=sorted({area_of[id(s.stop)] for s in stops}),
        slot=slot,
        day_trip=day_trip,
        stops=stops,
        minutes=round(total),
        walk_km=round(walk, 3),
        hours_unknown_count=sum(not s.hours_known for s in stops),
        photo_count=sum(s.stop.has_photo for s in stops),
    )
    if day_trip:
        straight = haversine_km(centre[0], centre[1], path[0][0].lat, path[0][0].lon)
        block.transfer_minutes = round(road_minutes(straight, rules.travel), 1)
        block.transfer_km = round(walk_km(straight, rules.travel), 3)
        block.transfer_method = "estimate"
    block.title = f"{SLOT_LABELS[slot]} around {path[0][0].name}"
    block.intro = _intro(block)
    return block


def _intro(block: Block) -> str:
    names = " → ".join(s.stop.name for s in block.stops)
    parts = [names, _duration_text(block.minutes)]
    if block.walk_km:
        parts.append(f"{block.walk_km:.1f} km on foot (estimate)")
    road = [s for s in block.stops if s.hop_method == "road"]
    if road:
        minutes = round(sum(s.hop_minutes for s in road))
        parts.append(f"{minutes} min by road between stops (estimate)")
    if block.day_trip:
        parts.append(
            f"about {round(block.transfer_minutes)} min each way by road from the city "
            "centre (estimate)"
        )
    if not block.walk_km and not road and not block.day_trip:
        parts.append("travel time is an estimate")
    return " · ".join(parts)


def _opposite(slot: str) -> str:
    return HALF_DAY_SLOTS[1 - HALF_DAY_SLOTS.index(slot)]


def _kind_slot(anchor: Stop, rules: BlockRules) -> str | None:
    if anchor.kind in rules.morning_kinds:
        return "morning"
    if anchor.kind in rules.afternoon_kinds:
        return "afternoon"
    return None


def _zones(areas: list[Area], far: set[int], rules: BlockRules) -> dict[int, list[Area]]:
    """Far areas within the pairing radius of each other share one zone (and one day trip).
    Keyed by the first area's number."""
    zones: dict[int, list[Area]] = {}
    for area in areas:
        if area.number not in far:
            continue
        for members in zones.values():
            head = members[0]
            if haversine_km(area.lat, area.lon, head.lat, head.lon) <= rules.blocks.pair_radius_km:
                members.append(area)
                break
        else:
            zones[area.number] = [area]
    return zones


def draft_blocks(stops: list[Stop], areas: list[Area], rules: BlockRules) -> BlockResult:
    result = BlockResult()
    if not stops:
        return result
    centre = (
        statistics.median(s.lat for s in stops),
        statistics.median(s.lon for s in stops),
    )
    area_of = {id(s): a.number for a in areas for s in a.members}
    far = {
        a.number for a in areas if haversine_km(a.lat, a.lon, *centre) > rules.blocks.day_trip_km
    }
    zones = _zones(areas, far, rules)
    in_zone = {a.number for members in zones.values() for a in members[1:]}
    leftovers: list[Stop] = []
    counters: dict[tuple[int, str], int] = {}
    alternate = 0

    def pick_slot(anchor: Stop) -> tuple[str, str]:
        nonlocal alternate
        slot = _kind_slot(anchor, rules)
        if slot is not None:
            return slot, "kind"
        slot = HALF_DAY_SLOTS[alternate % 2]
        alternate += 1
        return slot, "alternate"

    def all_open(path, slot: str) -> bool:
        window = rules.windows[slot]
        return all(is_open_in(p[0].hours, window) for p in path)

    def needs_full_day(path) -> bool:
        anchor = path[0][0]
        straight = haversine_km(centre[0], centre[1], anchor.lat, anchor.lon)
        visits = sum(_visit(p[0], rules)[3] for p in path) + sum(p[1] for p in path)
        return visits + 2 * road_minutes(straight, rules.travel) > rules.blocks.half_day_minutes

    def fit_slot(path, slot: str, basis: str, is_far: bool) -> tuple[str, str]:
        """The slot the finished block really fits. A far block is a full day only when its
        visits plus both transfers need one; a half-day slot must suit every stop's known hours
        (else the other half, else the full day); a block is flexible (`alternate`) only if it
        is open in both halves."""
        if is_far and needs_full_day(path):
            return "full_day", "trip"
        if not all_open(path, slot):
            other = _opposite(slot)
            return (other, "hours") if all_open(path, other) else ("full_day", "trip")
        if basis == "alternate" and not all_open(path, _opposite(slot)):
            basis = "hours"
        return slot, basis

    def emit(anchor, path, slot, basis, is_far, flags=()):
        slot, basis = fit_slot(path, slot, basis, is_far)
        block = _finish(anchor, path, slot, is_far, centre, area_of, rules)
        block.slot_basis = basis
        block.flags.extend(flags)
        key = (block.area_numbers[0], block.slot)
        counters[key] = counters.get(key, 0) + 1
        block.block_id = f"{block.owner_slug}-a{key[0]:02d}-{block.slot}-{counters[key]:02d}"
        result.blocks.append(block)

    def far_cap(anchor: Stop) -> float:
        straight = haversine_km(centre[0], centre[1], anchor.lat, anchor.lon)
        return rules.blocks.full_day_minutes - 2 * road_minutes(straight, rules.travel)

    for area in areas:
        if area.number in in_zone:
            continue
        if area.number in far:
            pool = sorted((s for a in zones[area.number] for s in a.members), key=priority_key)
            while pool:
                anchor = pool[0]
                if far_cap(anchor) < _visit(anchor, rules)[3]:
                    pool.pop(0)
                    result.unblocked.append((anchor, "too_far_for_day_trip"))
                    continue
                if not is_open_in(anchor.hours, rules.windows["full_day"]):
                    pool.pop(0)
                    result.unblocked.append((anchor, "closed_in_slot"))
                    continue
                path = _grow(
                    anchor, pool, "full_day", far_cap(anchor), rules, only_hops_within_walk=True
                )
                if path is None:
                    pool.pop(0)
                    leftovers.append(anchor)
                    continue
                slot, basis = pick_slot(anchor)
                emit(anchor, path, slot, basis, True)
                used = {id(p[0]) for p in path}
                result.unblocked.extend(
                    (s, "day_trip_area_limit") for s in pool if id(s) not in used
                )
                break
            continue
        pool = list(area.members)
        made = 0
        first_slot = ""
        while pool:
            if made >= rules.blocks.max_blocks_per_area:
                result.unblocked.extend((s, "area_block_limit") for s in pool)
                break
            anchor = pool[0]
            if made == 0:
                slot, basis = pick_slot(anchor)
                if not is_open_in(anchor.hours, rules.windows[slot]):
                    slot, basis = _opposite(slot), "hours"
            else:
                slot, basis = _opposite(first_slot), "opposite"
            if not is_open_in(anchor.hours, rules.windows[slot]):
                pool.pop(0)
                result.unblocked.append((anchor, "closed_in_slot"))
                continue
            path = _grow(
                anchor,
                pool,
                slot,
                float(rules.blocks.half_day_minutes),
                rules,
                only_hops_within_walk=True,
            )
            if path is None:
                pool.pop(0)
                leftovers.append(anchor)
                continue
            emit(anchor, path, slot, basis, False)
            first_slot = first_slot or slot
            made += 1
            used = {id(p[0]) for p in path}
            pool = [s for s in pool if id(s) not in used]

    leftovers.sort(key=priority_key)
    while leftovers:
        anchor = leftovers.pop(0)
        is_far = haversine_km(anchor.lat, anchor.lon, *centre) > rules.blocks.day_trip_km
        slot, basis = pick_slot(anchor)
        window = "full_day" if is_far else slot
        if is_far and far_cap(anchor) < _visit(anchor, rules)[3]:
            result.unblocked.append((anchor, "too_far_for_day_trip"))
            continue
        if not is_open_in(anchor.hours, rules.windows[window]):
            if is_far or not is_open_in(anchor.hours, rules.windows[_opposite(slot)]):
                result.unblocked.append((anchor, "closed_in_slot"))
                continue
            slot, basis, window = _opposite(slot), "hours", _opposite(slot)
        cap = far_cap(anchor) if is_far else float(rules.blocks.half_day_minutes)
        path = _grow(
            anchor,
            leftovers,
            window,
            cap,
            rules,
            only_hops_within_walk=False,
            radius_km=rules.blocks.pair_radius_km,
        )
        if path is None:
            result.unblocked.append((anchor, "isolated_stop"))
            continue
        used = {id(p[0]) for p in path}
        leftovers = [s for s in leftovers if id(s) not in used]
        emit(anchor, path, slot, basis, is_far, ("paired_isolated_stops",))
    return result


def days_fillable(blocks: list[Block]) -> int:
    """Whole days the blocks can fill with no stop repeated. A day is a morning block plus an
    afternoon block, or one full-day block. A block whose slot only came from alternating (its
    kind says nothing) can be either half."""
    full = sum(1 for b in blocks if b.slot == "full_day")
    halves = [b for b in blocks if b.slot != "full_day"]
    flexible = sum(1 for b in halves if b.slot_basis == "alternate")
    morning = sum(1 for b in halves if b.slot_basis != "alternate" and b.slot == "morning")
    afternoon = sum(1 for b in halves if b.slot_basis != "alternate" and b.slot == "afternoon")
    paired = min(morning, afternoon)
    spare = max(morning, afternoon) - paired
    used = min(flexible, spare)
    return full + paired + used + (flexible - used) // 2
