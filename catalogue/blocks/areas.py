"""Greedy, deterministic clustering of one city's stops into walkable areas.

Stops are taken in priority order (UNESCO, has a photo, has known hours, has a known kind, then
name). A stop joins the nearest existing area whose centre is within the walking radius (straight
line times the detour factor), else it starts a new area. The centre is the mean of the members, so
it moves as members join. Because the order is fixed, the same stops give the same areas.
"""

from collections.abc import Iterable

from catalogue.blocks.geo import haversine_km, road_minutes, walk_km, walk_minutes
from catalogue.blocks.model import Area, Stop
from catalogue.blocks.rules import BlockRules

TRAVEL_PAIR_KM = 5.0  # area pairs further apart than this (straight line) are not listed
TRAVEL_NEIGHBOURS = 15


def priority_key(stop: Stop) -> tuple:
    return (
        not stop.unesco,
        not stop.has_photo,
        stop.hours is None,
        stop.kind is None,
        stop.name.casefold(),
        stop.key,
        stop.page_id,
        stop.ordinal,
    )


def build_areas(stops: Iterable[Stop], rules: BlockRules) -> list[Area]:
    areas: list[Area] = []
    for stop in sorted(stops, key=priority_key):
        best: Area | None = None
        best_km = float("inf")
        for area in areas:
            km = walk_km(haversine_km(stop.lat, stop.lon, area.lat, area.lon), rules.travel)
            if km <= rules.areas.walk_radius_km and km < best_km:
                best, best_km = area, km
        if best is None:
            areas.append(Area(number=len(areas) + 1, members=[stop], lat=stop.lat, lon=stop.lon))
            continue
        best.members.append(stop)
        count = len(best.members)
        best.lat = sum(m.lat for m in best.members) / count
        best.lon = sum(m.lon for m in best.members) / count
    return areas


def area_travel(areas: list[Area], rules: BlockRules) -> list[dict]:
    """Estimated travel between nearby area centres: each area's nearest neighbours within 5 km
    (straight line). Every row is labelled an estimate."""
    rows = []
    for a in areas:
        near = []
        for b in areas:
            if a is b:
                continue
            km = haversine_km(a.lat, a.lon, b.lat, b.lon)
            if km <= TRAVEL_PAIR_KM:
                near.append((km, b.number))
        for km, number in sorted(near)[:TRAVEL_NEIGHBOURS]:
            rows.append(
                {
                    "from_area": a.number,
                    "to_area": number,
                    "straight_km": round(km, 3),
                    "walk_km": round(walk_km(km, rules.travel), 3),
                    "walk_minutes": round(walk_minutes(km, rules.travel), 1),
                    "road_minutes": round(road_minutes(km, rules.travel), 1),
                    "method": "estimate",
                }
            )
    return rows
