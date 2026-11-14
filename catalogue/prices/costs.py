"""Cost estimates (B11, the pure part): what a day block and a day cost, for each visitor group.

For each stop the fee is, in this order: the stop's exact (fresh) price for the group, else the
city's approximate band for the stop's kind, else unknown. An unknown fee is counted, never added
as zero. Food is one meal per half-day block (two for a full-day block) at the city's eat band,
only where that band exists. Local transport is never included: there is no fare data. Everything
is labelled `estimate` and carries a `basis` that says where each part came from. Amounts stay in
minor units of the city's own currency; nothing is converted between currencies.
"""

from typing import Any

from catalogue.prices.pricing import VISITOR_TIERS, ListingPrice, PriceRules

LABEL = "estimate"
CHECK_OFFICIAL = "Check on official site"
TRANSPORT_NOTE = "not included: no fare data"
Bands = dict[tuple[int, str, str, str], dict[str, Any]]


def _empty() -> dict[str, int]:
    return {"low_minor": 0, "high_minor": 0, "stops": 0}


def _exact_tier(price: ListingPrice, group: str, currency: str) -> tuple[int, int] | None:
    if price.status != "exact" or price.currency not in (None, currency):
        return None
    return price.tiers.get(group) or price.tiers.get("adult")


def _group_estimate(
    block: dict[str, Any],
    stops: list[dict[str, Any]],
    prices: dict[tuple[int, int], ListingPrice],
    bands: Bands,
    currency: str | None,
    rules: PriceRules,
    group: str,
) -> dict[str, Any]:
    exact, approx, unknown = _empty(), _empty(), 0
    city = block["owner_page_id"]
    band_use: dict[str, list[Any]] = {}
    edit_years: list[str] = []
    stop_basis: list[str] = []
    for stop in stops:
        price = (
            prices.get((stop["page_id"], stop["ordinal"])) if stop["source"] == "listing" else None
        )
        if price is None:
            unknown += 1
            stop_basis.append("unknown")
            continue
        tier = _exact_tier(price, group, currency)
        if tier is not None:
            exact["low_minor"] += tier[0]
            exact["high_minor"] += tier[1]
            exact["stops"] += 1
            stop_basis.append("exact")
            if price.lastedit_date:
                edit_years.append(price.lastedit_date)
            continue
        # A stop whose listing says nothing about a price is unknown, not given the city's band.
        band = None
        if currency is not None and price.status != "none":
            band = bands.get((city, price.kind, currency, group))
        if band is None:
            unknown += 1
            stop_basis.append("unknown")
            continue
        approx["low_minor"] += band["low_minor"]
        approx["high_minor"] += band["high_minor"]
        approx["stops"] += 1
        stop_basis.append("city_band")
        band_use.setdefault(price.kind, [band, 0])[1] += 1
    food = None
    eat = bands.get((city, "eat", currency, group)) if currency else None
    meals = rules.meals_full_day if block["slot"] == "full_day" else rules.meals_half_day
    if eat is not None and meals:
        food = {
            "low_minor": eat["low_minor"] * meals,
            "high_minor": eat["high_minor"] * meals,
            "meals": meals,
            "n_prices": eat["n_prices"],
        }
    parts = [exact, approx, food]
    present = [p for p in parts if p is not None and (p.get("stops", 1) > 0)]
    total_low = sum(p["low_minor"] for p in present) if present else None
    total_high = sum(p["high_minor"] for p in present) if present else None
    basis: list[str] = []
    if exact["stops"]:
        span = f"{min(edit_years)[:4]}-{max(edit_years)[:4]}"
        basis.append(f"{exact['stops']} stop(s) with an exact price (edited {span})")
    for kind, (band, count) in sorted(band_use.items()):
        basis.append(f"{count} stop(s) at the {kind} city band (n={band['n_prices']})")
    if food:
        basis.append(f"food: {food['meals']} meal(s) at the eat band (n={food['n_prices']})")
    if unknown:
        basis.append(f"{unknown} stop(s) with no price")
    basis.append("local transport not included")
    return {
        "currency": currency,
        "exact": exact,
        "approx": approx,
        "unknown_stops": unknown,
        "stop_basis": stop_basis,
        "food": food,
        "total_low_minor": total_low,
        "total_high_minor": total_high,
        # a total with an unknown stop only covers the stops that have a figure
        "complete": unknown == 0 and total_low is not None,
        "transport": TRANSPORT_NOTE,
        "note": CHECK_OFFICIAL if unknown or total_low is None else None,
        "basis": "; ".join(basis),
    }


def estimate_block(
    block: dict[str, Any],
    stops: list[dict[str, Any]],
    prices: dict[tuple[int, int], ListingPrice],
    bands: Bands,
    currency: str | None,
    rules: PriceRules,
) -> dict[str, Any]:
    """One estimate per visitor group (`resident`, `foreigner`) for one block."""
    result: dict[str, Any] = {"block_id": block["block_id"], "label": LABEL}
    for group in VISITOR_TIERS:
        result[group] = _group_estimate(block, stops, prices, bands, currency, rules, group)
    return result


def _sum(parts: list[int | None]) -> int | None:
    found = [p for p in parts if p is not None]
    return sum(found) if found else None


def day_cost(estimates: list[dict[str, Any]]) -> dict[str, Any]:
    """A day is the sum of its blocks' estimates; it stays unknown only if every block is."""
    result: dict[str, Any] = {"label": LABEL}
    for group in VISITOR_TIERS:
        each = [e[group] for e in estimates]
        unknown = sum(e["unknown_stops"] for e in each)
        total_low = _sum([e["total_low_minor"] for e in each])
        result[group] = {
            "currency": each[0]["currency"] if each else None,
            "exact_stops": sum(e["exact"]["stops"] for e in each),
            "approx_stops": sum(e["approx"]["stops"] for e in each),
            "unknown_stops": unknown,
            "food_meals": sum((e["food"] or {}).get("meals", 0) for e in each),
            "total_low_minor": total_low,
            "total_high_minor": _sum([e["total_high_minor"] for e in each]),
            "complete": bool(each) and all(e["complete"] for e in each),
            "transport": TRANSPORT_NOTE,
            "note": CHECK_OFFICIAL if unknown or total_low is None else None,
            "basis": f"sum of {len(each)} block estimate(s); local transport not included",
        }
    return result
