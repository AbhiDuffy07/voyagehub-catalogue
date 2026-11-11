"""Quality checks for listings. They flag; they never drop or change a listing's data."""

import math

EARTH_RADIUS_KM = 6371.0088


def parse_coordinate(lat: str, long: str) -> tuple[float, float] | None:
    """(latitude, longitude) when both are real numbers in range and not the 0,0 placeholder."""
    try:
        latitude, longitude = float(lat.strip()), float(long.strip())
    except ValueError:
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if (latitude, longitude) == (0.0, 0.0):
        return None
    return latitude, longitude


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometres (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))
