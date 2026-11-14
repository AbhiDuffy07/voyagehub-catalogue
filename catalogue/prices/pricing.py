"""Prices (B11, the pure part): which listing prices are exact, and the per-city bands.

A listing price is `exact` only when its text was fully read and its listing was last edited within
two years of the dump; then it keeps its visitor tiers (`resident`, `foreigner`, `adult`). Any other
price never shows as a figure. It may still feed a city band: the band is the median of the
amounts of one city, kind, currency and visitor group (the group's own tier, else the adult or
untiered amount), with the 25th to 75th percentile as its range, from the fresh prices alone when
there are enough of them. Nothing is converted
between currencies, filled in, or guessed; a city with too few prices gets no band.
"""

import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError
from catalogue.listings.lastedit import FRESH_YEARS, LastEdit, age_class
from catalogue.photos.select import _strings, _table

PRICE_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "price_rules.toml"
VISITOR_TIERS = ("resident", "foreigner")  # an untiered or adult amount is `adult`
_FRESH_AGE = "within"


@dataclass(frozen=True, slots=True)
class PriceRules:
    version: int
    fresh_years: int
    person_units: tuple[str, ...]
    sleep_units: tuple[str, ...]
    band_kinds: tuple[str, ...]
    min_prices: int
    low_percentile: int
    high_percentile: int
    meals_half_day: int
    meals_full_day: int


def _whole(table: dict[str, Any], key: str, source: str, *, minimum: int, maximum: int) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PipelineError(f"{source}: {key} needs a whole number from {minimum} to {maximum}")
    return value


def load_price_rules(path: Path = PRICE_RULES_PATH) -> PriceRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("price_rules_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PipelineError(f"{source}: price_rules_version needs a whole number of at least 1")
    units = _table(document, "units", source)
    bands = _table(document, "bands", source)
    meals = _table(document, "meals", source)
    return PriceRules(
        version=version,
        fresh_years=FRESH_YEARS,
        person_units=tuple(_strings(units, "person", source)),
        sleep_units=tuple(_strings(units, "sleep", source)),
        band_kinds=tuple(_strings(bands, "kinds", source)),
        min_prices=_whole(bands, "min_prices", source, minimum=1, maximum=1000),
        low_percentile=_whole(bands, "low_percentile", source, minimum=0, maximum=50),
        high_percentile=_whole(bands, "high_percentile", source, minimum=50, maximum=100),
        meals_half_day=_whole(meals, "half_day", source, minimum=0, maximum=10),
        meals_full_day=_whole(meals, "full_day", source, minimum=0, maximum=10),
    )


@dataclass(frozen=True, slots=True)
class ListingPrice:
    page_id: int
    ordinal: int
    owner_page_id: int
    kind: str
    status: str  # exact, qualified (fresh but "from"/"about"), not_fresh, unreadable, none
    age: str  # within, older, unclear, undated
    currency: str | None  # None for a free price, which has no currency
    tiers: dict[str, tuple[int, int]] = field(default_factory=dict)  # only when exact
    # per visitor group: its own tier's midpoint, else the adult or untiered one (minor units)
    band_values: dict[str, int] = field(default_factory=dict)
    lastedit_date: str | None = None
    price_text: str = ""


def _span(entry: dict[str, Any]) -> tuple[int, int] | None:
    if entry.get("free"):
        return 0, 0
    minor = entry.get("amount_minor")
    if isinstance(minor, int) and not isinstance(minor, bool):
        return minor, minor
    low, high = entry.get("amount_min_minor"), entry.get("amount_max_minor")
    if isinstance(low, int) and isinstance(high, int):
        return low, high
    return None


def _tiers(
    row: dict[str, Any], rules: PriceRules
) -> tuple[dict[str, tuple[int, int]], str | None, bool]:
    """The usable tiers of a fully read price, its one currency, and whether any amount is worded
    "from" or "about" (then it is not an exact price); empty tiers when none."""
    if row["price_status"] == "label_only" and row.get("price_is_free"):
        return {"adult": (0, 0)}, None, False
    if row["price_status"] != "parsed":
        return {}, None, False
    units = rules.sleep_units if row["kind"] == "sleep" else rules.person_units
    tiers: dict[str, tuple[int, int]] = {}
    currencies: set[str] = set()
    untiered = qualified = False
    for entry in row["price_entries"]:
        audience = entry.get("audience")
        tier = "adult" if audience in (None, "adult") else audience
        unit = entry.get("unit")
        if tier not in (*VISITOR_TIERS, "adult") or entry.get("label"):
            continue
        if unit is not None and unit not in units:
            continue
        span = _span(entry)
        if span is None:
            continue
        if not entry.get("free"):
            currency = entry.get("currency")
            if not currency or entry.get("currency_ambiguous"):
                return {}, None, False
            currencies.add(currency)
        # Two different amounts for one tier mean the text has more than one price list (a
        # composite ticket, a half price) that the parser could not tell apart: not read.
        if tiers.get(tier, span) != span:
            return {}, None, False
        tiers[tier] = span
        untiered = untiered or audience is None
        qualified = qualified or bool(entry.get("qualifier"))
    # An amount with no visitor type next to a resident or foreigner price is ambiguous too.
    if len(currencies) > 1 or (untiered and any(t in tiers for t in VISITOR_TIERS)):
        return {}, None, False
    return tiers, next(iter(currencies), None), qualified


def classify_listing(row: dict[str, Any], dump_day: date, rules: PriceRules) -> ListingPrice:
    edit = LastEdit(
        row.get("lastedit_status") or "missing",
        row.get("lastedit_date"),
        row.get("lastedit_precision"),
        row.get("lastedit_year"),
    )
    age = age_class(edit, dump_date=dump_day)
    base = {
        "page_id": row["page_id"],
        "ordinal": row["ordinal"],
        "owner_page_id": row["owner_page_id"],
        "kind": row["kind"],
        "age": age,
        "lastedit_date": edit.date if edit.status == "parsed" else None,
        "price_text": row.get("price") or "",
    }
    if row["price_status"] == "empty":
        return ListingPrice(status="none", currency=None, **base)
    tiers, currency, qualified = _tiers(row, rules)
    if not tiers:
        return ListingPrice(status="unreadable", currency=None, **base)
    values = {}
    for group in VISITOR_TIERS:
        span = tiers.get(group) or tiers.get("adult")
        if span is not None:
            values[group] = (span[0] + span[1]) // 2
    exact = age == _FRESH_AGE and not qualified
    status = "exact" if exact else "qualified" if age == _FRESH_AGE else "not_fresh"
    return ListingPrice(
        status=status,
        currency=currency,
        tiers=tiers if exact else {},
        band_values=values,
        **base,
    )


def _percentile(values: list[int], percent: int) -> int:
    """Linear interpolation between the closest ranks of a sorted list, in whole minor units."""
    position = (len(values) - 1) * percent / 100
    below = int(position)
    above = min(below + 1, len(values) - 1)
    return round(values[below] + (values[above] - values[below]) * (position - below))


def build_bands(
    prices: list[ListingPrice], rules: PriceRules
) -> dict[tuple[int, str, str, str], dict[str, Any]]:
    """Bands keyed by (city page id, kind, currency, visitor group). A free price has no currency
    and counts as zero in every band of its city, kind and group; a city with only free prices gets
    no band."""
    paid: dict[tuple[int, str, str, str], list[tuple[ListingPrice, int]]] = defaultdict(list)
    free: dict[tuple[int, str, str], list[tuple[ListingPrice, int]]] = defaultdict(list)
    for price in prices:
        if price.kind not in rules.band_kinds:
            continue
        for group, value in price.band_values.items():
            if price.currency is None:
                free[(price.owner_page_id, price.kind, group)].append((price, value))
            else:
                key = (price.owner_page_id, price.kind, price.currency, group)
                paid[key].append((price, value))
    bands: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    for key, found in paid.items():
        found = found + free.get((key[0], key[1], key[3]), [])
        fresh = [item for item in found if item[0].age == _FRESH_AGE]
        pool, basis = (fresh, "fresh") if len(fresh) >= rules.min_prices else (found, "all")
        if len(pool) < rules.min_prices:
            continue
        values = sorted(value for _, value in pool)
        dates = sorted(p.lastedit_date for p, _ in pool if p.lastedit_date)
        ages = [p.age for p, _ in found]
        bands[key] = {
            "owner_page_id": key[0],
            "kind": key[1],
            "currency": key[2],
            "group": key[3],
            "label": "approximate",
            "basis": basis,
            "median_minor": _percentile(values, 50),
            "low_minor": _percentile(values, rules.low_percentile),
            "high_minor": _percentile(values, rules.high_percentile),
            "n_prices": len(pool),
            "n_fresh": ages.count(_FRESH_AGE),
            "n_older": sum(a in ("older", "unclear") for a in ages),
            "n_undated": ages.count("undated"),
            "oldest_edit": dates[0] if dates else None,
            "newest_edit": dates[-1] if dates else None,
        }
    return bands
