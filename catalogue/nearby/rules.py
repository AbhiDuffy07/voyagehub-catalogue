"""`config/nearby.toml`: the rules of `nearby` (B18). Every value is checked on load; a bad file
stops the job with its name, never with a guess."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.enrich.kindmap import MAPPABLE_KINDS
from catalogue.errors import PipelineError

NEARBY_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "nearby.toml"
MAX_SEARCH_RADIUS_M = 150  # owner limit for Commons geosearch around a place
MAX_PLACES_RADIUS_KM = 10  # the GeoData API refuses a larger search radius
MAX_RESULT_LIMIT = 500  # the GeoData API's own limit per request


@dataclass(frozen=True, slots=True)
class NearbyRules:
    version: int
    geosearch_enabled: bool
    search_radius_m: int
    name_free_radius_m: int
    max_unnamed_files: int
    ambiguity_margin_m: int
    max_photos_per_place: int
    geosearch_limit: int
    places_radius_km: float
    places_limit: int
    kinds: frozenset[str]
    max_per_city: int
    same_place_km: float
    any_name_km: float
    cache_max_age_days: int


def _table(document: dict[str, Any], name: str, source: str) -> dict[str, Any]:
    table = document.get(name)
    if not isinstance(table, dict):
        raise PipelineError(f"{source}: the [{name}] table is missing")
    return table


def _number(
    table: dict[str, Any],
    key: str,
    source: str,
    *,
    whole: bool = True,
    low: float = 0,
    high: float | None = None,
    zero_ok: bool = False,
) -> Any:
    value = table.get(key)
    ok = isinstance(value, int) if whole else isinstance(value, int | float)
    if isinstance(value, bool) or not ok:
        raise PipelineError(f"{source}: {key} needs a {'whole ' if whole else ''}number")
    if value < low or (value == low and not zero_ok) or (high is not None and value > high):
        limit = f" up to {high:g}" if high is not None else ""
        raise PipelineError(f"{source}: {key} is out of range (above {low:g}{limit})")
    return value


def _reason(table: dict[str, Any], name: str, source: str) -> None:
    text = table.get("reason")
    if not isinstance(text, str) or not text.strip():
        raise PipelineError(f"{source}: [{name}] needs a written reason")


def load_nearby_rules(path: Path = NEARBY_RULES_PATH) -> NearbyRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PipelineError(f"{source} needs an integer rules_version")
    geo = _table(document, "geosearch", source)
    places = _table(document, "places", source)
    cache = _table(document, "cache", source)
    _reason(geo, "geosearch", source)
    _reason(places, "places", source)
    enabled = geo.get("enabled")
    if not isinstance(enabled, bool):
        raise PipelineError(f"{source}: enabled needs true or false")
    radius = _number(geo, "search_radius_m", source, high=MAX_SEARCH_RADIUS_M)
    name_free = _number(geo, "name_free_radius_m", source, high=radius, zero_ok=True)
    kinds = places.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(k in MAPPABLE_KINDS for k in kinds):
        raise PipelineError(f"{source}: kinds needs a non-empty list of place kinds")
    return NearbyRules(
        version=version,
        geosearch_enabled=enabled,
        search_radius_m=radius,
        name_free_radius_m=name_free,
        max_unnamed_files=_number(geo, "max_unnamed_files", source, zero_ok=True),
        ambiguity_margin_m=_number(geo, "ambiguity_margin_m", source, zero_ok=True),
        max_photos_per_place=_number(geo, "max_photos_per_place", source),
        geosearch_limit=_number(geo, "result_limit", source, high=MAX_RESULT_LIMIT),
        places_radius_km=float(
            _number(places, "radius_km", source, whole=False, high=MAX_PLACES_RADIUS_KM)
        ),
        places_limit=_number(places, "result_limit", source, high=MAX_RESULT_LIMIT),
        kinds=frozenset(kinds),
        max_per_city=_number(places, "max_per_city", source),
        same_place_km=float(_number(places, "same_place_km", source, whole=False)),
        any_name_km=float(_number(places, "any_name_km", source, whole=False, zero_ok=True)),
        cache_max_age_days=_number(cache, "max_age_days", source),
    )
