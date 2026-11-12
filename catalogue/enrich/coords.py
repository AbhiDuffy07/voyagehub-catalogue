"""When may a Wikidata coordinate fill a missing one? (B4, owner ruling 2026-11-12.)

The page's own Wikivoyage point always wins and is never changed. Wikidata is used only for a
place with no usable point, and only when nothing looks wrong. Whenever something does, the
coordinate stays empty, the Wikidata value is kept beside it, and a flag says why. Nothing is
guessed and nothing is silently repaired.

Blocking flags, always reported in this order when several apply:
  item_is_destination      the item is a destination's own item (the city, not a place in it)
  country_unconfirmed      Wikidata gives no country, or we have none to compare with
  wikidata_country_disagrees   Wikidata's country is not the page's country
  wikidata_coord_coarse    Wikidata says the point is known only to within a degree or so
  wikidata_coord_far       further from the page's own point than that page type allows
Item-level reasons (no coordinate to use): no_wikidata_id, bad_wikidata_id, item_missing,
item_merged, no_p625, multiple_coordinates, not_earth, invalid_wikidata_coordinate.
Informational (do not block): own_geo_invalid, no_page_point, no_far_limit (the page type has no
distance limit, so the fill was not distance-checked), wikidata_coord_disagrees.
"""

from collections.abc import Collection
from dataclasses import dataclass

from catalogue.enrich.claims import ItemFacts
from catalogue.listings.quality import distance_km

_COORDINATE_REASONS = {
    "none": "no_p625",
    "multiple": "multiple_coordinates",
    "not_earth": "not_earth",
    "invalid": "invalid_wikidata_coordinate",
}


@dataclass(frozen=True, slots=True)
class CoordDecision:
    filled: bool
    lat: float | None  # the coordinate to use, only when filled
    lon: float | None
    source: str | None  # "wikidata" when filled
    wikidata_lat: float | None  # kept even when not used
    wikidata_lon: float | None
    flags: tuple[str, ...]
    disagreement_km: float | None  # own point vs Wikidata point, when both exist


def country_check(item: ItemFacts | None, acceptable: Collection[str]) -> str:
    """`ok` when Wikidata's country (P17) is one the page's country allows, `disagrees` when it
    names other countries only, `unknown` when either side has nothing to compare."""
    if item is None or not item.countries or not acceptable:
        return "unknown"
    return "ok" if set(item.countries) & set(acceptable) else "disagrees"


def decide_coordinate(
    *,
    own_point: tuple[float, float] | None,
    own_invalid: bool,
    item: ItemFacts | None,
    item_problem: str | None,
    country: str,
    reference_point: tuple[float, float] | None,
    limit_km: float | None,
    needs_reference: bool,
    is_destination_item: bool,
    disagreement_km: float,
    max_precision: float,
) -> CoordDecision:
    """`own_point`: the page's or listing's usable Wikivoyage point. `own_invalid`: it has a value
    that is not usable. `reference_point` and `limit_km`: the owning page's point and the far
    limit for its type (a listing's check); `needs_reference` says whether a missing reference
    point deserves the `no_page_point` note (true for listings, false for destinations)."""
    usable = item is not None and item.coordinate_status == "ok"
    wikidata = item.coordinate if usable and item is not None else None
    w_lat, w_lon = (wikidata.lat, wikidata.lon) if wikidata else (None, None)

    def result(flags: tuple[str, ...], *, fill: bool = False, km: float | None = None):
        return CoordDecision(
            filled=fill,
            lat=w_lat if fill else None,
            lon=w_lon if fill else None,
            source="wikidata" if fill else None,
            wikidata_lat=w_lat,
            wikidata_lon=w_lon,
            flags=flags,
            disagreement_km=km,
        )

    if own_point is not None:
        if wikidata is None:
            return result(())
        km = distance_km(own_point, (wikidata.lat, wikidata.lon))
        return result(("wikidata_coord_disagrees",) if km > disagreement_km else (), km=km)

    informational = ("own_geo_invalid",) if own_invalid else ()
    if item is None:
        return result((*informational, item_problem or "no_wikidata_id"))
    if wikidata is None:
        return result((*informational, _COORDINATE_REASONS[item.coordinate_status]))

    blocking: list[str] = []
    if is_destination_item:
        blocking.append("item_is_destination")
    if country == "unknown":
        blocking.append("country_unconfirmed")
    elif country == "disagrees":
        blocking.append("wikidata_country_disagrees")
    if wikidata.precision is not None and wikidata.precision > max_precision:
        blocking.append("wikidata_coord_coarse")
    note: list[str] = []
    if reference_point is not None and limit_km is not None:
        if distance_km(reference_point, (wikidata.lat, wikidata.lon)) > limit_km:
            blocking.append("wikidata_coord_far")
    elif needs_reference:
        # Either the page has no point, or its type has no distance limit (a region, say). The fill
        # still needs the country to match, but it was not distance-checked, and the row says so.
        note.append("no_page_point" if reference_point is None else "no_far_limit")

    if blocking:
        return result((*informational, *blocking))
    return result((*informational, *note), fill=True)
