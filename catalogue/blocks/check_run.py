"""`auto-check` (B10): a status for every draft block, from B8's output alone.

Reads `out/blocks/<date>[-CC]/` (hash-checked against its manifest and country filter) and writes
`out/block_checks/<date>[-CC]/`: one row per block (`checked`, `flagged` or `rejected`, with its
reasons and the flagged stops), a per-city summary, a report and a seeded sample of flagged and
rejected blocks for the author's own review. It changes nothing in B8's files and drops nothing: a
flagged or rejected block keeps all its stops. Offline, deterministic, local files only.
"""

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from catalogue.blocks.build import days_fillable
from catalogue.blocks.check import (
    STATUSES,
    CheckRules,
    Verdict,
    check_block,
    load_check_rules,
    stop_identity,
)
from catalogue.blocks.inputs import _verify
from catalogue.blocks.rules import BlockRules, load_block_rules
from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.enrich.inputs import _rows
from catalogue.errors import PipelineError
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import _safe_cell, parse_dump_date

SCHEMA_VERSION = 1
SAMPLE_PER_STATUS = 10
REPORT_CITIES = 25
BLOCKS_FILE = "day_blocks.jsonl"
STOPS_FILE = "day_block_stops.jsonl"
OUTPUT_FILES = ("block_checks.jsonl", "city_summary.csv", "flagged_sample.md", "report.md")


@dataclass(frozen=True, slots=True)
class CheckResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        by = t["by_status"]
        lines = [
            f"blocks: {t['blocks']} (" + ", ".join(f"{s} {by.get(s, 0)}" for s in STATUSES) + ")",
            f"days the checked blocks can fill: {t['days_fillable_checked']} "
            f"(all blocks: {t['days_fillable_all']}); non-sight stops flagged: "
            f"{t['stops_flagged']}",
        ]
        if t["by_reason"]:
            lines.append(", ".join(f"{k} {v}" for k, v in t["by_reason"].items()))
        return "\n".join(lines)


def _slot_view(block: dict[str, Any]) -> SimpleNamespace:
    """`days_fillable` only needs a slot and how it was chosen."""
    return SimpleNamespace(slot=block["slot"], slot_basis=block.get("slot_basis", "kind"))


def _read(folder: Path) -> tuple[list[dict], dict[str, list[dict]]]:
    try:
        blocks = list(_rows(folder / BLOCKS_FILE))
        stops: dict[str, list[dict]] = defaultdict(list)
        for row in _rows(folder / STOPS_FILE):
            stops[row["block_id"]].append(row)
        for rows in stops.values():
            rows.sort(key=lambda r: r["position"])
        for block in blocks:
            for key in ("block_id", "slot", "minutes", "owner_slug"):
                if key not in block:
                    raise KeyError(key)
    except (KeyError, TypeError):
        raise PipelineError(f"{BLOCKS_FILE} or {STOPS_FILE} has a damaged row") from None
    return blocks, stops


def _sample_text(sample: list[tuple[dict, Verdict, list[dict]]]) -> str:
    lines = [
        f"# Flagged and rejected blocks, seeded sample ({len(sample)})",
        "",
        "For the author's own review. Nothing is removed from a block: a flagged stop is marked.",
        "",
    ]
    for block, verdict, stops in sample:
        flagged = {f["position"]: f["word"] for f in verdict.flagged}
        lines += [
            f"## {block['owner_title']}: {block['title']} ({verdict.status})",
            f"- reasons: {', '.join(verdict.failures)}",
            f"- {block['intro']}",
        ]
        for s in stops:
            mark = f"  <- non-sight: {flagged[s['position']]}" if s["position"] in flagged else ""
            lines.append(f"  {s['position']}. {s['name']} [{s['kind'] or 'kind unknown'}]{mark}")
        lines.append("")
    return "\n".join(lines)


def _report(totals: dict[str, Any], city_rows: list[dict[str, Any]], dump_date: str, rules) -> str:
    t = totals
    lines = [
        f"# auto-check, dump {dump_date}",
        "",
        f"- check rules version: {rules.version}; "
        f"blocks rules version: {t['blocks_rules_version']}",
        f"- blocks: {t['blocks']} in {t['cities']} cities",
        f"- days the checked blocks can fill: {t['days_fillable_checked']} "
        f"(all draft blocks: {t['days_fillable_all']})",
        "- Nothing is dropped: a flagged or rejected block keeps all its stops; "
        "B12 decides what is served (checked only).",
        "",
        "## Blocks by status",
        "",
        *[f"- `{s}`: {t['by_status'].get(s, 0)}" for s in STATUSES],
        "",
        "## Reasons",
        "",
        *([f"- `{k}`: {v}" for k, v in t["by_reason"].items()] or ["- none"]),
        "",
        f"## Non-sight words matched ({t['stops_flagged']} stops)",
        "",
        *([f"- `{k}`: {v}" for k, v in t["matched_words"].items()] or ["- none"]),
        "",
        "## For information (not failures)",
        "",
        f"- blocks where no stop has known hours: {t['blocks_all_hours_unknown']}",
        f"- blocks with no stop that has a photo: {t['blocks_without_photo']}",
        "",
        f"## Largest {REPORT_CITIES} cities by checked blocks (all cities in `city_summary.csv`)",
        "",
        "| city | blocks | checked | flagged | rejected | days (checked) | days (all) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in city_rows[:REPORT_CITIES]:
        city = str(r["city"]).replace("|", "\\|")
        lines.append(
            f"| {city} | {r['blocks']} | {r['checked']} | {r['flagged']} | {r['rejected']} "
            f"| {r['days_checked']} | {r['days_all']} |"
        )
    lines += ["", "`flagged_sample.md` is a seeded sample for the author's own review.", ""]
    return "\n".join(lines)


def run_auto_check(
    *,
    blocks_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: CheckRules | None = None,
    block_rules: BlockRules | None = None,
) -> CheckResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_check_rules()
    block_rules = block_rules if block_rules is not None else load_block_rules()
    country_filter = country_iso.upper() if country_iso else None
    hashes = {
        name: _verify(blocks_folder, name, "draft-blocks", country_filter)
        for name in (BLOCKS_FILE, STOPS_FILE)
    }
    try:
        blocks_rules_version = json.loads((blocks_folder / "manifest.json").read_text("utf-8"))[
            "rules_version"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError("the draft-blocks manifest.json is damaged; run it again") from None
    blocks, stops_by_block = _read(blocks_folder)

    owners_of: dict[tuple, set[str]] = defaultdict(set)
    for block in blocks:
        for stop in stops_by_block.get(block["block_id"], []):
            owners_of[stop_identity(stop)].add(block["block_id"])
    reused = frozenset(k for k, ids in owners_of.items() if len(ids) > 1)

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    status_count: Counter = Counter()
    reasons: Counter = Counter()
    words: Counter = Counter()
    city: dict[str, dict[str, Any]] = {}
    sample_pool: dict[str, list[tuple[dict, Verdict, list[dict]]]] = defaultdict(list)
    all_hours_unknown = without_photo = flagged_stops = 0
    with JsonlWriter(target / "block_checks.jsonl") as out:
        for block in blocks:
            stops = stops_by_block.get(block["block_id"], [])
            try:
                verdict = check_block(block, stops, block_rules, rules, reused)
            except (KeyError, TypeError):
                raise PipelineError(f"{STOPS_FILE} has a damaged row") from None
            out.write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "block_id": block["block_id"],
                    "owner_slug": block["owner_slug"],
                    "check_status": verdict.status,
                    "check_failures": verdict.failures,
                    "flagged_stops": verdict.flagged,
                    "check_rules_version": rules.version,
                    "blocks_rules_version": block.get("rules_version"),
                    "dump_date": dump_date,
                }
            )
            status_count[verdict.status] += 1
            reasons.update(verdict.failures)
            words.update(f["word"] for f in verdict.flagged)
            flagged_stops += len(verdict.flagged)
            all_hours_unknown += block.get("hours_unknown_count") == len(stops)
            without_photo += not block.get("photo_count")
            entry = city.setdefault(
                block["owner_slug"],
                {
                    "city": block.get("owner_title") or block["owner_slug"],
                    "all": [],
                    "ok": [],
                    "n": Counter(),
                },
            )
            entry["all"].append(_slot_view(block))
            entry["n"][verdict.status] += 1
            if verdict.status == "checked":
                entry["ok"].append(_slot_view(block))
            else:
                sample_pool[verdict.status].append((block, verdict, stops))

    city_rows = []
    for slug, e in city.items():
        city_rows.append(
            {
                "slug": slug,
                "city": e["city"],
                "blocks": len(e["all"]),
                "checked": e["n"]["checked"],
                "flagged": e["n"]["flagged"],
                "rejected": e["n"]["rejected"],
                "days_checked": days_fillable(e["ok"]),
                "days_all": days_fillable(e["all"]),
            }
        )
    city_rows.sort(key=lambda r: (-r["checked"], r["slug"]))
    rng = random.Random(10)  # noqa: S311 - a reproducible sample, not security
    sample = []
    for status in ("flagged", "rejected"):
        pool = sample_pool[status]
        sample += sorted(
            rng.sample(pool, min(SAMPLE_PER_STATUS, len(pool))), key=lambda x: x[0]["block_id"]
        )
    totals = {
        "blocks": len(blocks),
        "cities": len(city_rows),
        "by_status": {s: status_count[s] for s in STATUSES if status_count[s]},
        "by_reason": dict(sorted(reasons.items())),
        "matched_words": dict(sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))),
        "stops_flagged": flagged_stops,
        "days_fillable_checked": sum(r["days_checked"] for r in city_rows),
        "days_fillable_all": sum(r["days_all"] for r in city_rows),
        "blocks_all_hours_unknown": all_hours_unknown,
        "blocks_without_photo": without_photo,
        "blocks_rules_version": blocks_rules_version,
    }
    write_text(
        target / "city_summary.csv",
        _csv_text(
            [
                "owner_slug",
                "city",
                "blocks",
                "checked",
                "flagged",
                "rejected",
                "days_fillable_checked",
                "days_fillable_all",
            ],
            [
                [
                    _safe_cell(r["slug"]),
                    _safe_cell(r["city"]),
                    r["blocks"],
                    r["checked"],
                    r["flagged"],
                    r["rejected"],
                    r["days_checked"],
                    r["days_all"],
                ]
                for r in sorted(city_rows, key=lambda r: r["slug"])
            ],
        ),
    )
    write_text(target / "flagged_sample.md", _sample_text(sample))
    write_text(target / "report.md", _report(totals, city_rows, dump_date, rules))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "check_rules_version": rules.version,
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
    return CheckResult(target, totals)
