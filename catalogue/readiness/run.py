"""`readiness` (B12): which cities are ready (plannable or browse only), and what to publish.

Joins hash-checked outputs of earlier jobs for one dump and country filter: the destination
articles (`discover`, hash recorded by `fetch-listings`), `fetch-listings` (who owns each listing),
`wikipedia-summary`, `photo-select`, `derive-timezone` (the world run is accepted for a country
run), `draft-blocks` and `auto-check`. Writes `out/readiness/<date>[-CC]/`: one row per assessed
city, counts per rule and per country, a report, a seeded sample for the author's own review, and
`state.json`, which the next run reads (`--previous`) to unpublish only after repeated failures.
Offline and deterministic. The rules are in config/readiness_rules.toml; nobody approves a city by
hand. Error messages name files only (the logs are public).
"""

import csv
import hashlib
import io
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from catalogue.blocks.build import pair_days
from catalogue.blocks.check_run import _md
from catalogue.blocks.inputs import (
    ARTICLES_FILE,
    LISTINGS_FILE,
    PHOTOS_FILE,
    SLUGS_PATH,
    SUMMARIES_FILE,
    _read_slugs,
    _verify,
    _verify_articles,
    _whole,
)
from catalogue.discover.output import write_manifest, write_text
from catalogue.enrich.inputs import _rows, _sha256
from catalogue.errors import PipelineError
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import parse_dump_date
from catalogue.publish.hero import author_problem, load_hero_rules
from catalogue.readiness.rules import (
    FAILED_RULES,
    CityFacts,
    CityState,
    ReadinessRules,
    assess_city,
    decide_publication,
    load_readiness_rules,
)

SCHEMA_VERSION = 1
BLOCKS_FILE = "day_blocks.jsonl"
CHECKS_FILE = "block_checks.jsonl"
TIMEZONES_FILE = "destination_timezones.jsonl"
STATE_FILE = "state.json"
TIERS = ("plannable", "browse_only", "not_ready")
SAMPLE_PER_TIER = 5
SAMPLE_NEAR_MISS = 3
OUTPUT_FILES = (
    "city_readiness.jsonl",
    "by_rule.csv",
    "by_country.csv",
    "sample.md",
    "report.md",
    STATE_FILE,
)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        return "\n".join(
            [
                f"cities assessed: {t['assessed']} (not assessed, other article types: "
                f"{t['not_assessed_type']})",
                f"ready: {t['ready']} (plannable {t['plannable']}, browse only "
                f"{t['browse_only']}); not ready: {t['not_ready']}",
                f"publish {t['publish']}, keep {t['keep']}, pending unpublish "
                f"{t['keep_pending_unpublish']}, unpublish {t['unpublish']}, "
                f"blocked (no slug) {t['blocked_no_slug']}",
            ]
        )


def _damaged(name: str) -> PipelineError:
    return PipelineError(f"{name} has a damaged row")


def _verify_timezones(folder: Path, country_filter: str | None) -> str:
    """The time zones of a country run may come from the world run (country_filter null)."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError("manifest.json of derive-timezone not found; run derive-timezone first")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded_country = manifest["country_filter"]
        recorded = manifest["outputs_sha256"][TIMEZONES_FILE]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError("the derive-timezone manifest.json is damaged; run it again") from None
    if recorded_country not in (None, country_filter):
        raise PipelineError(
            "derive-timezone was run for a different country filter; use the world run or the "
            "same --country"
        )
    path = folder / TIMEZONES_FILE
    if not path.is_file():
        raise PipelineError(f"{TIMEZONES_FILE} not found; run derive-timezone first")
    digest = _sha256(path)
    if digest != recorded:
        raise PipelineError(f"{TIMEZONES_FILE} does not match the derive-timezone manifest")
    return digest


def _same_dump(folder: Path, job: str, dump_date: str) -> None:
    """Every input must come from the dump being assessed, not an older one."""
    if not (folder / "manifest.json").is_file():
        return  # the file check that follows says which job to run first
    try:
        recorded = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["dump_date"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None
    if recorded != dump_date:
        raise PipelineError(f"{job} was run for another dump; use the same --dump-date")


def _json_manifest(folder: Path, job: str) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None


def _state_entry(value: Any) -> CityState:
    published, failed = value["published"], value["failed_runs"]
    if not isinstance(published, bool) or not _whole(failed) or failed < 0:
        raise TypeError
    return CityState(published, failed)


def _read_previous(folder: Path, country_filter: str | None) -> dict[int, CityState]:
    path = folder / STATE_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["schema_version"] != SCHEMA_VERSION:
            raise ValueError
        recorded_country = document["country_filter"]
        cities = {int(key): _state_entry(value) for key, value in document["cities"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise PipelineError(f"{STATE_FILE} in the previous folder is missing or damaged") from None
    if recorded_country != country_filter:
        raise PipelineError("the previous readiness run used a different country filter")
    return cities


def _csv_text(header: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _sample_text(sample: list[dict[str, Any]]) -> str:
    lines = [
        "# Readiness sample",
        "",
        "Cities picked by a fixed seed for the author's own review: up to "
        f"{SAMPLE_PER_TIER} plannable, {SAMPLE_PER_TIER} browse only, and not-ready cities "
        "(near misses first).",
        "",
    ]
    for row in sample:
        failed = ", ".join(row["failed_rules"]) or "none"
        lines += [
            f"## {_md(row['title'])} ({row['wikivoyage_page_id']}, {row['country_iso']}): "
            f"{row['tier']}",
            "",
            f"- Wikivoyage status: {row['wikivoyage_status']}; failed rules: {failed}",
            f"- summary {'yes' if row['has_summary'] else 'no'}, time zone "
            f"{'yes' if row['has_timezone'] else 'no'}, hero photos {row['hero_photos']}, "
            f"photographed places {row['photographed_places']}",
            f"- checked blocks {row['checked_blocks']} (held back: "
            f"{row['flagged_or_rejected_blocks']}), whole days {row['days_fillable']}",
            f"- publication: {row['publish_action']}",
            "",
        ]
    return "\n".join(lines) + "\n"


def _report(totals: dict[str, Any], dump_date: str, rules: ReadinessRules) -> str:
    t = totals
    lines = [
        f"# Readiness, dump {dump_date}",
        "",
        f"Rules: readiness_rules_version {rules.version}. A city is ready with a summary, a time "
        f"zone, at least {rules.min_hero_photos} hero photo (2,000 px or more), at least "
        f"{rules.min_photographed_places} places with their own photo and at least "
        f"{rules.min_checked_blocks} checked block. Ready and filling "
        f"{rules.min_plannable_days} whole day is plannable; ready otherwise is browse only.",
        "",
        "## Cities",
        "",
        f"- assessed (article type {', '.join(rules.eligible_types)}): {t['assessed']}",
        f"- not assessed, other article types: {t['not_assessed_type']}",
        f"- ready: {t['ready']} (plannable {t['plannable']}, browse only {t['browse_only']})",
        f"- not ready: {t['not_ready']}",
        "- ready by Wikivoyage status: "
        + (", ".join(f"{k} {v}" for k, v in t["ready_by_status"].items()) or "none"),
        "",
        "## Failed rules (a city can fail several)",
        "",
        "| Rule | Failing | Failing only this rule |",
        "|---|---|---|",
    ]
    for rule in FAILED_RULES:
        lines.append(f"| {rule} | {t['failing'][rule]} | {t['only_failing'][rule]} |")
    lines += [
        "",
        "## Publication",
        "",
        f"- publish (newly ready): {t['publish']}; keep: {t['keep']}",
        f"- failed once, still published: {t['keep_pending_unpublish']}; unpublished after "
        f"{rules.unpublish_after_failed_runs} failed runs: {t['unpublish']}",
        f"- ready but not published, no slug in data/slugs.json: {t['blocked_no_slug']}",
        "  (the static page needs a slug; slugs are assigned only in a reviewed discover wave, "
        "so these wait for one)",
        f"- published earlier but not in this run (counted above as failed runs): "
        f"{t['missing_from_run']}",
        f"- published without a slug: {t['published_without_slug']} (a page keyed by slug "
        "cannot be built for these until a slug is assigned)",
        "",
        "Not required, by owner ruling (the design asked for them): Wikivoyage status usable, "
        "guide or star (outline cities may be ready), at least ten mappable sights, and a "
        "currency the exchange-rate source covers.",
        "",
    ]
    return "\n".join(lines)


def run_readiness(
    *,
    listings_folder: Path,
    summaries_folder: Path,
    photos_folder: Path,
    blocks_folder: Path,
    checks_folder: Path,
    timezones_folder: Path,
    discover_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    previous_folder: Path | None = None,
    rules: ReadinessRules | None = None,
    slugs_path: Path = SLUGS_PATH,
) -> ReadinessResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_readiness_rules()
    country_filter = country_iso.upper() if country_iso else None
    for folder, job in (
        (listings_folder, "fetch-listings"),
        (summaries_folder, "wikipedia-summary"),
        (photos_folder, "photo-select"),
        (blocks_folder, "draft-blocks"),
        (checks_folder, "auto-check"),
        (timezones_folder, "derive-timezone"),
    ):
        _same_dump(folder, job, dump_date)
    hashes = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter),
        ARTICLES_FILE: _verify_articles(discover_folder, listings_folder),
        SUMMARIES_FILE: _verify(
            summaries_folder, SUMMARIES_FILE, "wikipedia-summary", country_filter
        ),
        PHOTOS_FILE: _verify(photos_folder, PHOTOS_FILE, "photo-select", country_filter),
        BLOCKS_FILE: _verify(blocks_folder, BLOCKS_FILE, "draft-blocks", country_filter),
        CHECKS_FILE: _verify(checks_folder, CHECKS_FILE, "auto-check", country_filter),
        TIMEZONES_FILE: _verify_timezones(timezones_folder, country_filter),
    }
    try:
        blocks_rules_version = _json_manifest(blocks_folder, "draft-blocks")["rules_version"]
        checks_manifest = _json_manifest(checks_folder, "auto-check")
        checked_against = checks_manifest["blocks_rules_version"]
        checked_blocks_hash = checks_manifest["inputs_sha256"][BLOCKS_FILE]
    except (KeyError, TypeError):
        raise PipelineError("a manifest.json is damaged; run the job again") from None
    if blocks_rules_version != checked_against:
        raise PipelineError(
            "auto-check output was built from other draft-blocks rules; run auto-check again"
        )
    if checked_blocks_hash != hashes[BLOCKS_FILE]:
        raise PipelineError(
            "auto-check output was built from other draft blocks than these; run auto-check again"
        )
    previous = _read_previous(previous_folder, country_filter) if previous_folder else {}
    if previous_folder:
        hashes["previous_state.json"] = _sha256(previous_folder / STATE_FILE)
    try:
        hashes["slugs.json"] = _sha256(slugs_path)
    except OSError:
        raise PipelineError("slugs.json could not be read; it is the slug registry") from None

    try:
        articles = {
            r["wikivoyage_page_id"]: r
            for r in _rows(discover_folder / ARTICLES_FILE)
            if r.get("wikivoyage_page_id") is not None
            and (country_filter is None or r.get("tree_country_iso") == country_filter)
        }
    except (KeyError, TypeError):
        raise _damaged(ARTICLES_FILE) from None
    cities = {pid: a for pid, a in articles.items() if a.get("type") in rules.eligible_types}

    try:
        place_owner = {
            (r["page_id"], r["ordinal"]): r["owner_page_id"]
            for r in _rows(listings_folder / LISTINGS_FILE)
            if r["kind"] in rules.place_sections and not r["duplicate_of"]
        }
        has_summary = {
            r["page_id"]
            for r in _rows(summaries_folder / SUMMARIES_FILE)
            if r["subject"] == "article" and r.get("summary")
        }
        has_timezone = {
            r["wikivoyage_page_id"]
            for r in _rows(timezones_folder / TIMEZONES_FILE)
            if r.get("timezone")
        }
    except (KeyError, TypeError):
        raise _damaged(f"{LISTINGS_FILE}, {SUMMARIES_FILE} or {TIMEZONES_FILE}") from None

    hero: Counter[int] = Counter()
    places: Counter[int] = Counter()
    unowned_photos = 0
    author_rules = load_hero_rules()  # publish-static leaves out a place photo that fails them
    try:
        for r in _rows(photos_folder / PHOTOS_FILE):
            if not r.get("photo"):
                continue
            if r["subject"] == "article":
                owner = r["page_id"]
            else:
                owner = place_owner.get((r["page_id"], r["ordinal"]))
                if owner is None:
                    unowned_photos += 1  # a photo of a listing that is not a place
                    continue
                if author_problem(r["photo"], author_rules):
                    continue  # not published, so not a photographed place
                places[owner] += 1
            hero[owner] += bool(r.get("hero_quality"))
    except (KeyError, TypeError):
        raise _damaged(PHOTOS_FILE) from None

    try:
        blocks = list(_rows(blocks_folder / BLOCKS_FILE))
        status_of = {r["block_id"]: r["check_status"] for r in _rows(checks_folder / CHECKS_FILE)}
        if any(b["block_id"] not in status_of for b in blocks):
            raise PipelineError(
                "auto-check output does not cover every block; run auto-check again"
            )
        checked: dict[int, list[Any]] = defaultdict(list)
        held_back: Counter[int] = Counter()
        for b in sorted(blocks, key=lambda b: b["block_id"]):
            if status_of[b["block_id"]] == "checked":
                checked[b["owner_page_id"]].append(
                    SimpleNamespace(slot=b["slot"], slot_basis=b.get("slot_basis", "kind"))
                )
            else:
                held_back[b["owner_page_id"]] += 1
    except (KeyError, TypeError):
        raise _damaged(f"{BLOCKS_FILE} or {CHECKS_FILE}") from None
    slugs = _read_slugs(slugs_path)

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    results: list[dict[str, Any]] = []
    actions: Counter[str] = Counter()
    failing: Counter[str] = Counter()
    only_failing: Counter[str] = Counter()
    by_country: dict[str, Counter[str]] = defaultdict(Counter)
    ready_by_status: Counter[str] = Counter()
    new_state: dict[int, CityState] = {}
    with JsonlWriter(target / "city_readiness.jsonl") as out:
        for pid in sorted(cities):
            article = cities[pid]
            facts = CityFacts(
                has_summary=pid in has_summary,
                has_timezone=pid in has_timezone,
                hero_photos=hero[pid],
                photographed_places=places[pid],
                checked_blocks=len(checked[pid]),
                days_fillable=len(pair_days(checked[pid])),
            )
            verdict = assess_city(facts, rules)
            slug = slugs.get(pid)
            state, action = decide_publication(
                previous.get(pid), ready=verdict.ready, rules=rules, has_slug=slug is not None
            )
            new_state[pid] = state
            actions[action] += 1
            failing.update(verdict.failed)
            if len(verdict.failed) == 1:
                only_failing[verdict.failed[0]] += 1
            country = article.get("tree_country_iso") or "unknown"
            by_country[country]["assessed"] += 1
            by_country[country][verdict.tier] += 1
            by_country[country]["ready"] += verdict.ready
            if verdict.ready:
                ready_by_status[article.get("status") or "unknown"] += 1
            row = {
                "schema_version": SCHEMA_VERSION,
                "readiness_rules_version": rules.version,
                "blocks_rules_version": blocks_rules_version,
                "dump_date": dump_date,
                "wikivoyage_page_id": pid,
                "slug": slug[0] if slug else None,
                "title": article.get("title"),
                "country_iso": article.get("tree_country_iso"),
                "wikivoyage_status": article.get("status"),
                "tier": verdict.tier,
                "ready": verdict.ready,
                "failed_rules": list(verdict.failed),
                "has_summary": facts.has_summary,
                "has_timezone": facts.has_timezone,
                "hero_photos": facts.hero_photos,
                "photographed_places": facts.photographed_places,
                "checked_blocks": facts.checked_blocks,
                "flagged_or_rejected_blocks": held_back[pid],
                "days_fillable": facts.days_fillable,
                "publish_action": action,
                "published": state.published,
                "failed_runs": state.failed_runs,
            }
            results.append(row)
            out.write(row)
    published_without_slug = sum(1 for r in results if r["published"] and r["slug"] is None)
    missing = {pid: s for pid, s in previous.items() if pid not in cities and s.published}
    for pid, old in missing.items():
        new_state[pid], action = decide_publication(old, ready=False, rules=rules)
        actions[action] += 1
    state_document = {
        "schema_version": SCHEMA_VERSION,
        "country_filter": country_filter,
        "cities": {
            str(pid): {"published": s.published, "failed_runs": s.failed_runs}
            for pid, s in sorted(new_state.items())
        },
    }
    write_text(target / STATE_FILE, json.dumps(state_document, sort_keys=True, indent=1) + "\n")

    tiers = Counter(r["tier"] for r in results)
    totals = {
        "assessed": len(results),
        "not_assessed_type": len(articles) - len(cities),
        "ready": tiers["plannable"] + tiers["browse_only"],
        "plannable": tiers["plannable"],
        "browse_only": tiers["browse_only"],
        "not_ready": tiers["not_ready"],
        "ready_by_status": dict(sorted(ready_by_status.items())),
        "failing": {rule: failing[rule] for rule in FAILED_RULES},
        "only_failing": {rule: only_failing[rule] for rule in FAILED_RULES},
        "publish": actions["publish"],
        "keep": actions["keep"],
        "keep_pending_unpublish": actions["keep_pending_unpublish"],
        "unpublish": actions["unpublish"],
        "blocked_no_slug": actions["blocked_no_slug"],
        "missing_from_run": len(missing),
        "published_without_slug": published_without_slug,
        "photos_of_non_places": unowned_photos,
    }
    write_text(
        target / "by_rule.csv",
        _csv_text(
            ["rule", "failing", "only_failing"],
            [[rule, failing[rule], only_failing[rule]] for rule in FAILED_RULES],
        ),
    )
    write_text(
        target / "by_country.csv",
        _csv_text(
            ["country_iso", "assessed", "ready", "plannable", "browse_only", "not_ready"],
            [
                [c, n["assessed"], n["ready"], n["plannable"], n["browse_only"], n["not_ready"]]
                for c, n in sorted(by_country.items())
            ],
        ),
    )
    rng = random.Random(12)  # noqa: S311 - a reproducible sample, not security
    chosen: list[dict[str, Any]] = []
    for tier in ("plannable", "browse_only"):
        pool = [r for r in results if r["tier"] == tier]
        chosen += rng.sample(pool, min(SAMPLE_PER_TIER, len(pool)))
    not_ready = [r for r in results if r["tier"] == "not_ready"]
    near = [r for r in not_ready if len(r["failed_rules"]) == 1]
    chosen += rng.sample(near, min(SAMPLE_NEAR_MISS, len(near)))
    rest = [r for r in not_ready if r not in chosen]
    chosen += rng.sample(rest, min(SAMPLE_PER_TIER - SAMPLE_NEAR_MISS, len(rest)))
    sample = sorted(chosen, key=lambda r: (TIERS.index(r["tier"]), r["wikivoyage_page_id"]))
    write_text(target / "sample.md", _sample_text(sample))
    write_text(target / "report.md", _report(totals, dump_date, rules))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "readiness_rules_version": rules.version,
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
    return ReadinessResult(target, totals)
