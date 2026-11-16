"""`price-bands` (B11): exact prices, per-city approximate bands, and cost estimates per block/day.

Reads three hash-checked folders, all for the same dump and country filter: `fetch-listings`
(prices), `draft-blocks` (blocks and stops) and `auto-check` (block status). Writes
`out/prices/<date>[-CC]/`: `listing_prices.jsonl` (exact prices with their tiers and source, plus
the status of every other price), `city_price_bands.jsonl`, `block_costs.jsonl`,
`day_costs.jsonl` (days of checked blocks only), a report and a seeded sample for the author's own
review. Offline, deterministic, local files only. Every figure is an estimate and says what it is
based on; a price that is missing stays missing ("Check on official site").
"""

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from catalogue.blocks.build import pair_days
from catalogue.blocks.check_run import BLOCKS_FILE, STOPS_FILE, _md, _read
from catalogue.blocks.inputs import LISTINGS_FILE, _verify
from catalogue.discover.output import write_manifest, write_text
from catalogue.enrich.inputs import _rows
from catalogue.errors import PipelineError
from catalogue.listings.lastedit import LastEdit, age_class
from catalogue.listings.output import JsonlWriter
from catalogue.listings.reference import load_country_currency, load_reference
from catalogue.listings.run import parse_dump_date
from catalogue.prices import fees as fees_module
from catalogue.prices.costs import CHECK_OFFICIAL, day_cost, estimate_block
from catalogue.prices.fees import FILE_NAME as FEES_FILE
from catalogue.prices.fees import OfficialFee, load_official_fees
from catalogue.prices.pricing import (
    VISITOR_TIERS,
    ListingPrice,
    PriceRules,
    build_bands,
    classify_listing,
    load_price_rules,
)

SCHEMA_VERSION = 1
CHECKS_FILE = "block_checks.jsonl"
SAMPLE_BLOCKS = 20
SAMPLE_SPLIT = (("complete", 7), ("partial", 7), ("no_data", 6))
OUTPUT_FILES = (
    "listing_prices.jsonl",
    "city_price_bands.jsonl",
    "block_costs.jsonl",
    "day_costs.jsonl",
    "cost_sample.md",
    "report.md",
)


@dataclass(frozen=True, slots=True)
class PriceResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        return "\n".join(
            [
                f"listings with a price: {t['listings_priced']} (exact {t['listings_exact']}, "
                f"from/about {t['listings_qualified']}, "
                f"older or undated {t['listings_not_fresh']}, "
                f"unreadable {t['listings_unreadable']})",
                f"city bands: {t['bands']} in {t['band_cities']} cities",
                f"blocks: {t['blocks']}; days from checked blocks: {t['days']}",
            ]
        )


def _manifest(folder: Path, job: str) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None


def _money(minor: int, currency: str | None, exponents: dict[str, int]) -> str:
    exponent = exponents.get(currency or "", 2)
    value = Decimal(minor).scaleb(-exponent)
    text = f"{value:,.0f}" if minor % (10**exponent) == 0 else f"{value:,.{exponent}f}"
    return f"{currency} {text}" if currency else text


def _range(low: int | None, high: int | None, currency: str | None, exps: dict[str, int]) -> str:
    if low is None or high is None:
        return CHECK_OFFICIAL
    if low == high:
        return _money(low, currency, exps)
    return f"{_money(low, currency, exps)} to {_money(high, currency, exps)}"


def _completeness(group: dict[str, Any]) -> str:
    """No data when no stop has a price or band (a food figure alone says nothing about stops)."""
    if group["exact"]["stops"] + group["approx"]["stops"] == 0:
        return "no_data"
    return "complete" if group["unknown_stops"] == 0 else "partial"


OFFICIAL_LICENCE = "Public fee notice (a fact, not a creative work)"


def _apply_fees(
    listed: list[tuple[dict[str, Any], ListingPrice]],
    fees: dict[tuple[str, str], OfficialFee],
    exponents: dict[str, int],
    dump_day: date,
) -> tuple[
    list[tuple[dict[str, Any], ListingPrice]], dict[tuple[int, int], OfficialFee], list[str]
]:
    """An official fee replaces the Wikivoyage price of its listing: exact when it was read within
    two years of the dump, else older. Returns the new list, the fees used by listing, and the
    fees whose listing was not found (a typo, or another country)."""
    used: dict[tuple[int, int], OfficialFee] = {}
    out = []
    for row, price in listed:
        fee = fees.get((str(row.get("owner_slug")), row["listing_key"]))
        if fee is None:
            out.append((row, price))
            continue
        exponent = exponents.get(fee.currency)
        minor = {}
        for tier, amount in (("resident", fee.resident), ("foreigner", fee.foreigner)):
            scaled = Decimal(amount) * 10 ** (exponent or 0)
            if exponent is None or scaled != scaled.to_integral_value():
                raise PipelineError(f"{FEES_FILE}: an amount does not fit its currency's units")
            minor[tier] = int(scaled)
        age = age_class(
            LastEdit("parsed", fee.date_read.isoformat(), "day", fee.date_read.year),
            dump_date=dump_day,
        )
        fresh = age == "within"
        tiers = {tier: (value, value) for tier, value in minor.items()}
        used[(price.page_id, price.ordinal)] = fee
        out.append(
            (
                row,
                replace(
                    price,
                    status="exact" if fresh else "not_fresh",
                    age=age,
                    currency=fee.currency,
                    tiers=tiers if fresh else {},
                    band_values=dict(minor),
                    lastedit_date=fee.date_read.isoformat(),
                    price_text=f"official fee, read {fee.date_read.isoformat()}",
                ),
            )
        )
    unmatched = sorted(
        f"{k[0]}/{k[1]}"
        for k in fees
        if (k not in {(r.get("owner_slug"), r["listing_key"]) for r, _ in listed})
    )
    return out, used, unmatched


def _listing_row(
    row: dict[str, Any],
    price: ListingPrice,
    rules: PriceRules,
    fee: OfficialFee | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "listing_key": row["listing_key"],
        "page_id": price.page_id,
        "ordinal": price.ordinal,
        "owner_page_id": price.owner_page_id,
        "kind": price.kind,
        "status": price.status,
        "age": price.age,
        "currency": price.currency,
        "lastedit_date": price.lastedit_date,
        "feeds_band": bool(price.band_values) and price.kind in rules.band_kinds,
    }
    if price.status == "exact":
        out["tiers"] = {
            tier: {"low_minor": low, "high_minor": high}
            for tier, (low, high) in price.tiers.items()
        }
        out["price_text"] = price.price_text
        for key in ("source_url", "revision_id", "licence", "licence_url", "attribution"):
            out[key] = row.get(key)
        if fee is not None:
            out.update(
                fee_source="official",
                source_url=fee.source_url,
                revision_id=None,
                licence=OFFICIAL_LICENCE,
                licence_url=None,
                attribution=f"Official fee page, read {fee.date_read.isoformat()}",
            )
    return out


def _read_listings(
    folder: Path, dump_day: date, rules: PriceRules
) -> list[tuple[dict[str, Any], ListingPrice]]:
    try:
        listed = []
        for row in _rows(folder / LISTINGS_FILE):
            row["listing_key"], row["price_status"]  # noqa: B018 - a missing field is damage
            listed.append((row, classify_listing(row, dump_day, rules)))
        return listed
    except (KeyError, TypeError, ValueError):
        raise PipelineError(f"{LISTINGS_FILE} has a damaged row") from None


def _sample_text(
    sample: list[dict[str, Any]],
    stops_by_block: dict[str, list[dict[str, Any]]],
    listing_by_key: dict[tuple[int, int], ListingPrice],
    exps: dict[str, int],
) -> str:
    lines = [
        "# Cost estimate sample",
        "",
        "Every figure is an estimate. `exact` = a price edited within two years; `city_band` = the "
        "city's approximate range for the kind; `unknown` = Check on official site.",
        "",
    ]
    for cost in sample:
        lines += [f"## {_md(cost['block_id'])} ({_md(cost['owner_slug'])}, {cost['check_status']})"]
        lines.append("")
        group = cost["foreigner"]
        for stop, basis in zip(stops_by_block[cost["block_id"]], group["stop_basis"], strict=True):
            price = listing_by_key.get((stop["page_id"], stop["ordinal"]))
            text = f" | text: {_md(price.price_text)!r}" if price and price.price_text else ""
            edited = f", edited {price.lastedit_date or 'unknown'}" if text else ""
            lines.append(f"- {stop['position']}. {_md(stop['name'])}: {basis}{text}{edited}")
        for name in VISITOR_TIERS:
            g = cost[name]
            food = g["food"]
            food_text = (
                f"food {_range(food['low_minor'], food['high_minor'], g['currency'], exps)}"
                if food
                else "no food figure"
            )
            total = _range(g["total_low_minor"], g["total_high_minor"], g["currency"], exps)
            if g["total_low_minor"] is not None and not g["complete"]:
                total += " (partial: only the stops and food with a figure)"
            lines.append(f"- **{name}** estimate: {total}; {food_text}; basis: {_md(g['basis'])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _report(
    totals: dict[str, Any],
    dump_date: str,
    rules: PriceRules,
    fees: list[OfficialFee],
    unmatched: list[str],
) -> str:
    t = totals
    lines = [
        f"# Prices and cost estimates, dump {dump_date}",
        "",
        f"Rules: price_rules_version {rules.version}. Every cost figure is an estimate; local "
        "transport is never included (no fare data). A price edited within two years is exact; "
        "older or undated prices only feed the per-city bands.",
        "",
        "## Listing prices",
        "",
        f"- listings with price text: {t['listings_priced']}",
        f"- exact (edited within two years, fully read): {t['listings_exact']}",
        f'- fresh but worded "from" or "about" (not exact, feeds bands): {t["listings_qualified"]}',
        f"- older or undated (only feed bands): {t['listings_not_fresh']}",
        f"- not usable as a number (partial, labels, unreadable units or currency): "
        f"{t['listings_unreadable']}",
        f"- of those, edited within two years but not read confidently (partial text, several "
        f"price lists, unknown visitor type): {t['listings_fresh_unreadable']}",
        "",
        "## City bands (approximate)",
        "",
        f"- bands (city, kind, currency): {t['bands']} in {t['band_cities']} cities; counted per "
        f"visitor group, from fresh prices only: {t['bands_fresh']}, pooled with older or "
        f"undated: {t['bands_pooled']}",
        "- by kind: " + (", ".join(f"{k} {v}" for k, v in t["bands_by_kind"].items()) or "none"),
        f"- cities with an eat band (food estimates possible): {t['eat_band_cities']}",
        "",
        "## Block estimates",
        "",
        f"Blocks: {t['blocks']}. Per visitor group, how complete the estimate is "
        "(complete = every stop has a price or band; partial = some stops say "
        f'"{CHECK_OFFICIAL}"; no data = no stop has a price or band).',
        "",
        "| Group | Complete | Partial | No data |",
        "|---|---|---|---|",
    ]
    for group in VISITOR_TIERS:
        c = t["blocks_by_completeness"][group]
        lines.append(f"| {group} | {c['complete']} | {c['partial']} | {c['no_data']} |")
    lines += [
        "",
        f"- stops with an exact price: {t['stops_exact']}, at a city band: {t['stops_band']}, "
        f'unknown ("{CHECK_OFFICIAL}"): {t["stops_unknown"]} (foreigner view)',
        f"- days from checked blocks: {t['days']}; with a figure: {t['days_with_figure']}",
        "",
        "## Official fees (overrides/monument_fees.csv)",
        "",
        f"- used: {t['official_fees_applied']}; not found in the listings: "
        f"{t['official_fees_unmatched']}",
        *[
            f"- used: {_md(f.owner_slug)}/{_md(f.listing_key)}, read {f.date_read.isoformat()}"
            for f in sorted(fees, key=lambda f: (f.owner_slug, f.listing_key))
        ],
        *[f"- NOT FOUND (check the key): {_md(key)}" for key in unmatched],
        "",
    ]
    return "\n".join(lines)


def run_price_bands(
    *,
    listings_folder: Path,
    blocks_folder: Path,
    checks_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: PriceRules | None = None,
    fees_path: Path | None = None,
) -> PriceResult:
    dump_day = parse_dump_date(dump_date)
    rules = rules if rules is not None else load_price_rules()
    country_filter = country_iso.upper() if country_iso else None
    hashes = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter),
        BLOCKS_FILE: _verify(blocks_folder, BLOCKS_FILE, "draft-blocks", country_filter),
        STOPS_FILE: _verify(blocks_folder, STOPS_FILE, "draft-blocks", country_filter),
        CHECKS_FILE: _verify(checks_folder, CHECKS_FILE, "auto-check", country_filter),
    }
    try:
        blocks_rules_version = _manifest(blocks_folder, "draft-blocks")["rules_version"]
        checked_against = _manifest(checks_folder, "auto-check")["blocks_rules_version"]
    except (KeyError, TypeError):
        raise PipelineError("a manifest.json is damaged; run the job again") from None
    if blocks_rules_version != checked_against:
        raise PipelineError(
            "auto-check output was built from other draft-blocks rules; run auto-check again"
        )

    reference = load_reference()
    exps = reference.exponents
    currency_of = load_country_currency()
    listed = _read_listings(listings_folder, dump_day, rules)
    fees_file = fees_path if fees_path is not None else fees_module.FEES_PATH
    fees = load_official_fees(fees_file, today=today)
    if fees_file.is_file():
        hashes[FEES_FILE] = hashlib.sha256(fees_file.read_bytes()).hexdigest()
    listed, used_fees, unmatched_fees = _apply_fees(listed, fees, exps, dump_day)
    city_currency: dict[int, str | None] = {}
    city_title: dict[int, str] = {}
    for row, price in listed:
        info = currency_of.get(str(row.get("country_iso") or "").upper())
        city_currency.setdefault(price.owner_page_id, info.code if info else None)
        city_title.setdefault(price.owner_page_id, row.get("owner_title") or "")
    # An exact price in another currency than its city's is not shown as exact (nothing is
    # converted); it still feeds the band of its own currency.
    listed = [
        (
            row,
            replace(price, status="other_currency", tiers={})
            if price.status == "exact"
            and price.currency not in (None, city_currency[price.owner_page_id])
            else price,
        )
        for row, price in listed
    ]
    prices = [price for _, price in listed]
    listing_by_key = {(p.page_id, p.ordinal): p for p in prices}
    bands = build_bands(prices, rules)

    blocks, stops_by_block = _read(blocks_folder)
    try:
        statuses = {r["block_id"]: r["check_status"] for r in _rows(checks_folder / CHECKS_FILE)}
        missing = [b["block_id"] for b in blocks if b["block_id"] not in statuses]
        owner_of = {b["block_id"]: b["owner_page_id"] for b in blocks}
    except (KeyError, TypeError):
        raise PipelineError("a block or check file has a damaged row") from None
    if missing:
        raise PipelineError("auto-check output does not cover every block; run auto-check again")

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    counts: Counter[str] = Counter()
    fresh_unreadable = sum(
        1
        for row, p in listed
        if p.status == "unreadable" and p.age == "within" and row["price_status"] != "label_only"
    )
    with JsonlWriter(target / "listing_prices.jsonl") as out:
        for row, price in sorted(listed, key=lambda x: (x[1].page_id, x[1].ordinal)):
            counts[price.status] += 1
            if price.status != "none":
                out.write(
                    _listing_row(row, price, rules, used_fees.get((price.page_id, price.ordinal)))
                )
    with JsonlWriter(target / "city_price_bands.jsonl") as out:
        for key in sorted(bands):
            out.write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "owner_title": city_title.get(key[0], ""),
                    **bands[key],
                }
            )

    estimates: dict[str, dict[str, Any]] = {}
    completeness: dict[str, Counter[str]] = {g: Counter() for g in VISITOR_TIERS}
    stop_counts: Counter[str] = Counter()
    with JsonlWriter(target / "block_costs.jsonl") as out:
        for block in sorted(blocks, key=lambda b: b["block_id"]):
            estimate = estimate_block(
                block | {"owner_page_id": owner_of[block["block_id"]]},
                stops_by_block.get(block["block_id"], []),
                listing_by_key,
                bands,
                city_currency.get(owner_of[block["block_id"]]),
                rules,
            )
            estimates[block["block_id"]] = estimate
            for group in VISITOR_TIERS:
                completeness[group][_completeness(estimate[group])] += 1
            stop_counts.update(estimate["foreigner"]["stop_basis"])
            out.write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "price_rules_version": rules.version,
                    "blocks_rules_version": blocks_rules_version,
                    "dump_date": dump_date,
                    "owner_page_id": owner_of[block["block_id"]],
                    "owner_slug": block["owner_slug"],
                    "check_status": statuses[block["block_id"]],
                    **estimate,
                }
            )

    by_city: dict[str, list[Any]] = defaultdict(list)
    for block in blocks:
        if statuses[block["block_id"]] == "checked":
            by_city[block["owner_slug"]].append(
                SimpleNamespace(
                    block_id=block["block_id"],
                    slot=block["slot"],
                    slot_basis=block.get("slot_basis", "kind"),
                )
            )
    day_total = days_with_figure = 0
    with JsonlWriter(target / "day_costs.jsonl") as out:
        for slug in sorted(by_city):
            ordered = sorted(by_city[slug], key=lambda b: b.block_id)
            days = sorted(
                ([b.block_id for b in day] for day in pair_days(ordered)), key=lambda ids: ids[0]
            )
            for ids in days:
                cost = day_cost([estimates[i] for i in ids])
                day_total += 1
                days_with_figure += (
                    cost["foreigner"]["exact_stops"] + cost["foreigner"]["approx_stops"] > 0
                )
                out.write(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "price_rules_version": rules.version,
                        "dump_date": dump_date,
                        "day_id": "day:" + "+".join(ids),
                        "owner_slug": slug,
                        "block_ids": ids,
                        **cost,
                    }
                )

    pools = Counter(b["basis"] for b in bands.values())
    totals = {
        "listings_priced": sum(n for s, n in counts.items() if s != "none"),
        "listings_exact": counts["exact"],
        "listings_qualified": counts["qualified"],
        "listings_not_fresh": counts["not_fresh"],
        "listings_unreadable": counts["unreadable"] + counts["other_currency"],
        "listings_fresh_unreadable": fresh_unreadable,
        "bands": len({k[:3] for k in bands}),
        "band_cities": len({k[0] for k in bands}),
        "bands_fresh": pools["fresh"],
        "bands_pooled": pools["all"],
        "bands_by_kind": dict(sorted(Counter(k[1] for k in {k[:3] for k in bands}).items())),
        "eat_band_cities": len({k[0] for k in bands if k[1] == "eat"}),
        "blocks": len(blocks),
        "blocks_by_completeness": {
            g: {c: completeness[g][c] for c in ("complete", "partial", "no_data")}
            for g in VISITOR_TIERS
        },
        "stops_exact": stop_counts["exact"],
        "stops_band": stop_counts["city_band"],
        "stops_unknown": stop_counts["unknown"],
        "days": day_total,
        "days_with_figure": days_with_figure,
        "official_fees_applied": len(used_fees),
        "official_fees_unmatched": len(unmatched_fees),
    }

    rng = random.Random(11)  # noqa: S311 - a reproducible sample, not security
    pool: dict[str, list[str]] = defaultdict(list)
    for block_id in sorted(estimates):
        pool[_completeness(estimates[block_id]["foreigner"])].append(block_id)
    chosen: list[str] = []
    for name, size in SAMPLE_SPLIT:
        chosen += rng.sample(pool[name], min(size, len(pool[name])))
    leftover = sorted(set(estimates) - set(chosen))
    chosen += rng.sample(leftover, min(SAMPLE_BLOCKS - len(chosen), len(leftover)))
    slug_of = {b["block_id"]: b["owner_slug"] for b in blocks}
    sample = [
        estimates[b] | {"owner_slug": slug_of[b], "check_status": statuses[b]}
        for b in sorted(chosen)
    ]
    write_text(
        target / "cost_sample.md", _sample_text(sample, stops_by_block, listing_by_key, exps)
    )
    write_text(
        target / "report.md",
        _report(totals, dump_date, rules, list(used_fees.values()), unmatched_fees),
    )
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "price_rules_version": rules.version,
            "blocks_rules_version": blocks_rules_version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": today.isoformat(),
            "inputs_sha256": hashes,
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return PriceResult(target, totals)
