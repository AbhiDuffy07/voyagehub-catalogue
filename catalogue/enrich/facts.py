"""Per-item facts decided from a parsed Wikidata item: UNESCO status, kind, time zone, entry fees.

Every function returns a value or an empty result plus flags; none of them guesses.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from catalogue.enrich.claims import ItemFacts

WORLD_HERITAGE_SITE = "Q9259"  # label checked against live Wikidata at the start of every run
FRESH_YEARS = 2  # the design's 2-year rule for prices, applied to counting only


def unesco_status(item: ItemFacts | None) -> tuple[bool, tuple[str, ...]]:
    """True only when the item has the World Heritage designation (P1435) AND a World Heritage ID
    (P757). Having just one of them is flagged and is not enough: a mistake in either would
    otherwise decide the UNESCO badge."""
    if item is None:
        return False, ()
    designated = WORLD_HERITAGE_SITE in item.heritage
    identified = bool(item.whc_ids)
    if designated and identified:
        return True, ()
    if designated or identified:
        return False, ("unesco_inconsistent",)
    return False, ()


def kind_of(
    item: ItemFacts | None, kind_map: Mapping[str, str]
) -> tuple[str | None, tuple[str, ...]]:
    """The place kind from the item's classes. None when no class is mapped; None and a flag when
    mapped classes disagree."""
    if item is None:
        return None, ()
    kinds = {kind_map[q] for q in item.instance_of if q in kind_map}
    if not kinds:
        return None, ()
    if len(kinds) > 1:
        return None, ("kind_ambiguous",)
    return next(iter(kinds)), ()


@dataclass(frozen=True, slots=True)
class TimezoneResult:
    name: str | None  # an IANA name like Asia/Kolkata
    basis: str | None  # wikidata_item, or country_single_zone when borrowed from the country
    flags: tuple[str, ...]


def _zone_names(zone_qids: tuple[str, ...], zone_items: Mapping[str, ItemFacts]) -> set[str]:
    return {name for q in zone_qids if q in zone_items for name in zone_items[q].iana_zones}


def timezone_of(
    place: ItemFacts | None, zone_items: Mapping[str, ItemFacts], country: ItemFacts | None
) -> TimezoneResult:
    """The IANA time zone: the place's own single zone, else the country's when it has exactly
    one (labelled), else empty with a flag. Two zones are never resolved by picking one."""
    if place is not None and place.time_zones:
        names = _zone_names(place.time_zones, zone_items)
        if len(names) == 1:
            return TimezoneResult(next(iter(names)), "wikidata_item", ())
        return TimezoneResult(None, None, ("timezone_ambiguous" if names else "timezone_unknown",))
    if country is not None:
        names = _zone_names(country.time_zones, zone_items)
        if len(names) == 1:
            return TimezoneResult(next(iter(names)), "country_single_zone", ())
    return TimezoneResult(None, None, ("timezone_unknown",))


@dataclass(frozen=True, slots=True)
class FeeSummary:
    """What Wikidata's fee statements (P2555) amount to, for counting. No amount is carried: no
    fee value is used anywhere until the owner has seen the counts."""

    has_fee: bool
    dated: bool  # some statement has a point in time, start or end
    within_two_years: bool  # the freshest such date is within 2 years before the data date
    currencies: tuple[str, ...]  # ISO codes of the fee units that are known currencies


def _two_years_before(day: date) -> date:
    try:
        return day.replace(year=day.year - FRESH_YEARS)
    except ValueError:  # 29 February
        return day.replace(year=day.year - FRESH_YEARS, day=28)


def fee_summary(
    item: ItemFacts | None, units: Mapping[str, ItemFacts], data_date: date
) -> FeeSummary:
    if item is None or not item.fees:
        return FeeSummary(False, False, False, ())
    dates: list[date] = []
    currencies: set[str] = set()
    for fee in item.fees:
        for text in (fee.point_in_time, fee.start, fee.end):
            if text:
                try:
                    dates.append(date.fromisoformat(text))
                except ValueError:
                    continue  # e.g. a year-only or impossible date: counts as not dated
        unit = units.get(fee.unit_qid) if fee.unit_qid else None
        if unit is not None:
            currencies.update(unit.currency_codes)
    freshest = max(dates, default=None)
    fresh = freshest is not None and _two_years_before(data_date) <= freshest <= data_date
    return FeeSummary(True, bool(dates), fresh, tuple(sorted(currencies)))
