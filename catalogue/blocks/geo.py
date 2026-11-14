"""Distances and travel times. Everything here is an estimate: straight line times a detour factor.

B7 (real routing) is deferred, so no figure may be shown without the word "estimate".
"""

import math

from catalogue.blocks.rules import TravelRules

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def walk_km(straight_km: float, travel: TravelRules) -> float:
    return straight_km * travel.detour_factor


def walk_minutes(straight_km: float, travel: TravelRules) -> float:
    return walk_km(straight_km, travel) / travel.walk_kmh * 60


def road_minutes(straight_km: float, travel: TravelRules) -> float:
    return walk_km(straight_km, travel) / travel.road_kmh * 60
