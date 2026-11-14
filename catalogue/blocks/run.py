"""`draft-blocks` (B8): areas and draft day blocks for every city, offline, estimates only.

Reads the fetch-listings, enrich-wikidata and photo-select outputs (each hash-checked against its
manifest) and writes `out/blocks/<date>[-CC]/`: the areas, the estimated travel between nearby area
centres, the draft blocks with their stops, the stops that could not be blocked (with the reason),
a per-city summary, a report and a seeded sample for the author's own review. Nothing is guessed:
a stop without a trusted point is counted and left out, hours that are unknown never exclude a
stop and never become a time, and every travel figure is labelled an estimate (B7 is deferred).
"""

import hashlib
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Any

from catalogue.blocks.areas import area_travel, build_areas
from catalogue.blocks.build import days_fillable, draft_blocks
from catalogue.blocks.inputs import input_hashes, read_stops
from catalogue.blocks.model import Block, Stop
from catalogue.blocks.rules import BlockRules, load_block_rules
from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import _safe_cell, parse_dump_date

SCHEMA_VERSION = 1
SAMPLE_SIZE = 20
REPORT_CITIES = 25
OUTPUT_FILES = (
    "area_travel.jsonl",
    "areas.jsonl",
    "city_summary.csv",
    "day_block_stops.jsonl",
    "day_blocks.jsonl",
    "report.md",
    "spot_check_sample.md",
    "unblocked_stops.csv",
)


@dataclass(frozen=True, slots=True)
class BlocksResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        lines = [
            f"stops used: {t['stops']} (left out: {sum(t['excluded'].values())}); "
            f"areas: {t['areas']}; blocks: {t['blocks']} in {t['cities_with_blocks']} cities "
            f"({t['by_slot_text']}); days they can fill: {t['days_fillable']}",
            f"not in a block: {t['unblocked_total']} ("
            + ", ".join(f"{k} {v}" for k, v in t["unblocked"].items())
            + ")",
        ]
        return "\n".join(lines)


def _area_id(slug: str, number: int) -> str:
    return f"{slug}-a{number:02d}"


def _block_row(b: Block, owner: Stop, rules: BlockRules, dump_date: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "block_id": b.block_id,
        "owner_page_id": b.owner_id,
        "owner_slug": b.owner_slug,
        "owner_title": owner.owner_title,
        "area_ids": [_area_id(b.owner_slug, n) for n in b.area_numbers],
        "slot": b.slot,
        "slot_basis": b.slot_basis,
        "day_trip": b.day_trip,
        "status": "draft",
        "method": "estimate",
        "title": b.title,
        "intro": b.intro,
        "stop_count": len(b.stops),
        "minutes": b.minutes,
        "walk_km": b.walk_km,
        "photo_count": b.photo_count,
        "hours_unknown_count": b.hours_unknown_count,
        "transfer_minutes": b.transfer_minutes,
        "transfer_km": b.transfer_km,
        "transfer_method": b.transfer_method,
        "flags": sorted(b.flags),
        "rules_version": rules.version,
        "dump_date": dump_date,
    }


def _stop_rows(b: Block) -> list[dict[str, Any]]:
    return [
        {
            "block_id": b.block_id,
            "position": s.position,
            "page_id": s.stop.page_id,
            "ordinal": s.stop.ordinal,
            "listing_key": s.stop.key,
            "name": s.stop.name,
            "lat": s.stop.lat,
            "long": s.stop.lon,
            "kind": s.stop.kind,
            "minutes_min": s.minutes_min,
            "minutes_max": s.minutes_max,
            "duration_basis": s.duration_basis,
            "hop_minutes": s.hop_minutes,
            "hop_km": s.hop_km,
            "hop_method": s.hop_method,
            "hours_known": s.hours_known,
            "has_photo": s.stop.has_photo,
        }
        for s in b.stops
    ]


def _sample_text(blocks: list[tuple[Block, str]], rules: BlockRules) -> str:
    lines = [
        f"# Spot-check sample ({len(blocks)} blocks, seeded)",
        "",
        "For the author's own review. Every figure is an estimate.",
        "",
    ]
    for b, city in blocks:
        lines += [
            f"## {city}: {b.title} ({b.slot}{', day trip' if b.day_trip else ''})",
            f"- {b.intro}",
            f"- stops {len(b.stops)}, unknown hours {b.hours_unknown_count}, "
            f"photos {b.photo_count}",
        ]
        for s in b.stops:
            hop = f" (+{s.hop_minutes:g} min {s.hop_method})" if s.hop_method else ""
            lines.append(
                f"  {s.position}. {s.stop.name} [{s.stop.kind or 'kind unknown'}; "
                f"{s.minutes_min}-{s.minutes_max} min {s.duration_basis}; "
                f"hours {'known' if s.hours_known else 'unknown'}; "
                f"{'photo' if s.stop.has_photo else 'no photo'}]{hop}"
            )
        lines.append("")
    return "\n".join(lines)


def _report(totals: dict[str, Any], city_rows: list[list[Any]], dump_date: str, rules) -> str:
    t = totals
    lines = [
        f"# draft-blocks, dump {dump_date}",
        "",
        f"- rules version: {rules.version} (stops per block {rules.blocks.min_stops}-"
        f"{rules.blocks.max_stops}; half day {rules.blocks.half_day_minutes} min)",
        "- **Every travel time and distance is an estimate**: straight line x "
        f"{rules.travel.detour_factor}, {rules.travel.walk_kmh} km/h on foot; road hops assume "
        f"{rules.travel.road_kmh} km/h. No routing (B7 is deferred).",
        f"- stops used: {t['stops']}; areas: {t['areas']}; blocks: {t['blocks']} "
        f"({t['by_slot_text']}), in {t['cities_with_blocks']} cities",
        f"- days the blocks can fill (no stop repeated; two half-days or one day trip per day): "
        f"{t['days_fillable']}",
        "",
        "## Listings left out (never guessed)",
        "",
        *([f"- `{k}`: {v}" for k, v in t["excluded"].items()] or ["- none"]),
        "",
        "## Stops that are in no block",
        "",
        *([f"- `{k}`: {v}" for k, v in t["unblocked"].items()] or ["- none"]),
        "",
        "## Cities by days they can fill",
        "",
        *[f"- {label}: {count}" for label, count in t["days_histogram"].items()],
        "",
        f"## Largest {REPORT_CITIES} cities (all cities in `city_summary.csv`)",
        "",
        "| city | stops | areas | blocks | days they can fill | not in a block |",
        "|---|---|---|---|---|---|",
    ]
    for row in city_rows[:REPORT_CITIES]:
        slug, title, stops, areas, blocks, days, unblocked = row[:7]
        city = str(title).replace("|", "\\|")
        lines.append(f"| {city} | {stops} | {areas} | {blocks} | {days} | {unblocked} |")
    lines += [
        "",
        "Hours: a stop is skipped for a slot only when its own parsed hours say it is closed; "
        "unknown hours are allowed and counted per block (`hours_unknown_count`). "
        "`spot_check_sample.md` is a seeded sample for the author's own review.",
        "",
    ]
    return "\n".join(lines)


def _histogram(days: list[int]) -> dict[str, int]:
    buckets = {"0 days": 0, "1-2 days": 0, "3-5 days": 0, "6+ days": 0}
    for d in days:
        key = "0 days" if d == 0 else "1-2 days" if d <= 2 else "3-5 days" if d <= 5 else "6+ days"
        buckets[key] += 1
    return buckets


def run_draft_blocks(
    *,
    listings_folder: Path,
    enrich_folder: Path,
    photos_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: BlockRules | None = None,
) -> BlocksResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_block_rules()
    country_filter = country_iso.upper() if country_iso else None
    hashes = input_hashes(listings_folder, enrich_folder, photos_folder, country_filter)
    stops, excluded = read_stops(
        listings_folder, enrich_folder, photos_folder, country_filter, rules
    )
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)

    by_slot: Counter = Counter()
    unblocked_total: Counter = Counter()
    city_rows: list[list[Any]] = []
    all_blocks: list[tuple[Block, str]] = []
    area_count = 0
    block_count = 0
    with (
        JsonlWriter(target / "areas.jsonl") as areas_out,
        JsonlWriter(target / "area_travel.jsonl") as travel_out,
        JsonlWriter(target / "day_blocks.jsonl") as blocks_out,
        JsonlWriter(target / "day_block_stops.jsonl") as stops_out,
    ):
        unblocked_csv: list[list[Any]] = []
        for _, group in groupby(stops, key=lambda s: s.owner_id):
            owner_stops = list(group)
            owner = owner_stops[0]
            areas = build_areas(owner_stops, rules)
            result = draft_blocks(owner_stops, areas, rules)
            area_count += len(areas)
            for area in areas:
                areas_out.write(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "area_id": _area_id(owner.owner_slug, area.number),
                        "owner_page_id": owner.owner_id,
                        "owner_slug": owner.owner_slug,
                        "name": f"Around {area.anchor.name}",
                        "lat": round(area.lat, 6),
                        "long": round(area.lon, 6),
                        "stop_count": len(area.members),
                        "members": [f"{m.page_id}:{m.ordinal}" for m in area.members],
                        "rules_version": rules.version,
                        "dump_date": dump_date,
                    }
                )
            for row in area_travel(areas, rules):
                travel_out.write(
                    {
                        **row,
                        "from_area": _area_id(owner.owner_slug, row["from_area"]),
                        "to_area": _area_id(owner.owner_slug, row["to_area"]),
                    }
                )
            for block in result.blocks:
                blocks_out.write(_block_row(block, owner, rules, dump_date))
                for row in _stop_rows(block):
                    stops_out.write(row)
                by_slot[block.slot] += 1
                all_blocks.append((block, owner.owner_title))
            block_count += len(result.blocks)
            days = days_fillable(result.blocks)
            for stop, reason in result.unblocked:
                unblocked_total[reason] += 1
                unblocked_csv.append(
                    [
                        _safe_cell(owner.owner_slug),
                        stop.page_id,
                        stop.ordinal,
                        _safe_cell(stop.key),
                        _safe_cell(stop.name),
                        reason,
                    ]
                )
            city_rows.append(
                [
                    owner.owner_slug,
                    owner.owner_title,
                    len(owner_stops),
                    len(areas),
                    len(result.blocks),
                    days,
                    len(result.unblocked),
                    sum(1 for b in result.blocks if b.day_trip),
                ]
            )
    city_rows.sort(key=lambda r: (-r[4], -r[2], r[0]))
    sample = random.Random(8).sample(  # noqa: S311 - a reproducible sample, not security
        all_blocks, min(SAMPLE_SIZE, len(all_blocks))
    )
    sample.sort(key=lambda item: item[0].block_id)
    totals = {
        "stops": len(stops),
        "excluded": dict(sorted(excluded.items())),
        "areas": area_count,
        "blocks": block_count,
        "cities_with_blocks": sum(1 for r in city_rows if r[4]),
        "by_slot": dict(sorted(by_slot.items())),
        "by_slot_text": ", ".join(f"{k} {v}" for k, v in sorted(by_slot.items())) or "none",
        "days_fillable": sum(r[5] for r in city_rows),
        "days_histogram": _histogram([r[5] for r in city_rows]),
        "unblocked": dict(sorted(unblocked_total.items())),
        "unblocked_total": sum(unblocked_total.values()),
    }
    write_text(
        target / "unblocked_stops.csv",
        _csv_text(
            ["owner_slug", "page_id", "ordinal", "listing_key", "name", "reason"], unblocked_csv
        ),
    )
    write_text(
        target / "city_summary.csv",
        _csv_text(
            [
                "owner_slug",
                "city",
                "stops",
                "areas",
                "blocks",
                "days_fillable",
                "unblocked_stops",
                "day_trips",
            ],
            [
                [_safe_cell(r[0]), _safe_cell(r[1]), *r[2:]]
                for r in sorted(city_rows, key=lambda r: r[0])
            ],
        ),
    )
    write_text(target / "report.md", _report(totals, city_rows, dump_date, rules))
    write_text(target / "spot_check_sample.md", _sample_text(sample, rules))
    read_at = today.isoformat()
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "rules_version": rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "inputs_sha256": hashes,
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return BlocksResult(target, totals)
