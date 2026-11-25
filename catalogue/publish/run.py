"""`publish-static` (B13): the static JSON site for the published cities.

Reads the hash-checked outputs of earlier jobs for one dump and country filter, and writes
`out/publish/<date>[-CC]/site/` (the folder that is deployed): `v1/cities/<slug>.json`,
`v1/index.json`, `v1/attributions.json` (credits in `v1/attributions/part-NNN.json`),
`v1/manifest.json` (version and hashes), `_headers` and a landing page `index.html`. A city is
written only when readiness says published and it has a slug. Every file is validated against its
pydantic model and size-checked before anything is written. Offline and deterministic. Error
messages name files only (the logs are public).
"""

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.blocks.inputs import (
    LISTINGS_FILE,
    PHOTOS_FILE,
    SUMMARIES_FILE,
    _verify,
)
from catalogue.enrich.inputs import _rows
from catalogue.errors import PipelineError
from catalogue.listings.run import parse_dump_date
from catalogue.nearby.inputs import PLACES_FILE as NEARBY_PLACES_FILE
from catalogue.nearby.inputs import extra_places, nearby_for
from catalogue.publish import build
from catalogue.publish.dump import dump_document
from catalogue.publish.index import index_files
from catalogue.publish.limits import PublishLimits, load_limits
from catalogue.publish.models import (
    SCHEMA_VERSION,
    AttributionsFile,
    AttributionsPart,
    CityFile,
    ManifestFile,
)
from catalogue.publish.site import HEADERS, LANDING_PAGE, version_of
from catalogue.readiness.rules import ReadinessRules, load_readiness_rules
from catalogue.readiness.run import (
    BLOCKS_FILE,
    CHECKS_FILE,
    TIMEZONES_FILE,
    _same_dump,
    _verify_timezones,
)

READINESS_FILE = "city_readiness.jsonl"
STOPS_FILE = "day_block_stops.jsonl"
SECTIONS_FILE = "sections.jsonl"
BANDS_FILE = "city_price_bands.jsonl"
COSTS_FILE = "block_costs.jsonl"
DAY_COSTS_FILE = "day_costs.jsonl"
API = "v1"
CREDIT_KEYS = ("author", "licence", "licence_url", "source_url", "attribution", "history_url")


@dataclass(frozen=True, slots=True)
class PublishResult:
    output_dir: Path
    site_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        return "\n".join(
            [
                f"cities published: {t['cities']} (skipped, published but no slug: "
                f"{t['skipped_no_slug']})",
                f"files: {t['files']}, total {t['total_bytes']} bytes, largest city file "
                f"{t['largest_city_bytes']} bytes",
                f"getting around: {t['with_getting_around_excerpt']} with an excerpt, "
                f"{t['with_ride_apps']} with ride apps of {t['cities']} cities",
                f"city photos not used as hero (replaced by a place photo or none): "
                f"{t['own_hero_rejected']}",
                f"thin cities: {t['without_hero']} without a hero photo, "
                f"{t['without_summary']} without a summary, {t['not_ready_published']} "
                "published but not ready now",
                f"photos {t['photos']}, summaries {t['summaries']}; left out for missing credit: "
                f"{t['photos_left_out_no_credit']} photos, "
                f"{t['photos_left_out_bad_author']} photos (author rule), "
                f"{t['summaries_left_out_no_credit']} summaries",
            ]
        )


def _verify_readiness(folder: Path, country_filter: str | None, dump_date: str) -> dict[str, Any]:
    _same_dump(folder, "readiness", dump_date)
    _verify(folder, READINESS_FILE, "readiness", country_filter)
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError("the readiness manifest.json is damaged; run readiness again") from None


def _chunks(entries: list[dict[str, Any]], target: int) -> list[list[dict[str, Any]]]:
    """Consecutive entries in parts of about `target` bytes (one entry may exceed it alone)."""
    parts: list[list[dict[str, Any]]] = []
    size = 0
    for entry in entries:
        text = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        length = len(text.encode("utf-8")) + 1
        if not parts or size + length > target:
            parts.append([])
            size = 0
        parts[-1].append(entry)
        size += length
    return parts


def _unique(rows: Iterable[dict[str, Any]], key: Any, name: str) -> dict[Any, dict[str, Any]]:
    found: dict[Any, dict[str, Any]] = {}
    for r in rows:
        k = key(r)
        if k in found:
            raise PipelineError(f"{name} has more than one row for the same key")
        found[k] = r
    return found


def _manifest(folder: Path, job: str) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None


def _lineage(manifest: dict[str, Any], job: str, digests: dict[str, str]) -> None:
    """A job must have been built from exactly the input files that are being published."""
    try:
        recorded = manifest["inputs_sha256"]
        for name, digest in digests.items():
            if recorded[name] != digest:
                raise PipelineError(
                    f"{job} was built from other {name.removesuffix('.jsonl')} than these; "
                    f"run {job} again"
                )
    except (KeyError, TypeError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None


def _group(rows: Iterable[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[r[key]].append(r)
    return grouped


def run_publish_static(
    *,
    readiness_folder: Path,
    listings_folder: Path,
    summaries_folder: Path,
    photos_folder: Path,
    blocks_folder: Path,
    checks_folder: Path,
    timezones_folder: Path,
    prices_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    limits: PublishLimits | None = None,
    rules: ReadinessRules | None = None,
    nearby_folder: Path | None = None,
) -> PublishResult:
    parse_dump_date(dump_date)
    limits = limits if limits is not None else load_limits()
    rules = rules if rules is not None else load_readiness_rules()
    country_filter = country_iso.upper() if country_iso else None
    for folder, job in (
        (listings_folder, "fetch-listings"),
        (summaries_folder, "wikipedia-summary"),
        (photos_folder, "photo-select"),
        (blocks_folder, "draft-blocks"),
        (checks_folder, "auto-check"),
        (timezones_folder, "derive-timezone"),
        (prices_folder, "price-bands"),
    ):
        _same_dump(folder, job, dump_date)
    readiness_manifest = _verify_readiness(readiness_folder, country_filter, dump_date)
    digests = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter),
        SUMMARIES_FILE: _verify(
            summaries_folder, SUMMARIES_FILE, "wikipedia-summary", country_filter
        ),
        SECTIONS_FILE: _verify(listings_folder, SECTIONS_FILE, "fetch-listings", country_filter),
        PHOTOS_FILE: _verify(photos_folder, PHOTOS_FILE, "photo-select", country_filter),
        BLOCKS_FILE: _verify(blocks_folder, BLOCKS_FILE, "draft-blocks", country_filter),
        STOPS_FILE: _verify(blocks_folder, STOPS_FILE, "draft-blocks", country_filter),
        CHECKS_FILE: _verify(checks_folder, CHECKS_FILE, "auto-check", country_filter),
        TIMEZONES_FILE: _verify_timezones(timezones_folder, country_filter),
    }
    for name in (BANDS_FILE, COSTS_FILE, DAY_COSTS_FILE):
        _verify(prices_folder, name, "price-bands", country_filter)
    _lineage(
        readiness_manifest,
        "readiness",
        {n: digests[n] for n in (LISTINGS_FILE, SUMMARIES_FILE, PHOTOS_FILE, BLOCKS_FILE)}
        | {CHECKS_FILE: digests[CHECKS_FILE], TIMEZONES_FILE: digests[TIMEZONES_FILE]},
    )
    nearby = nearby_for(_manifest(photos_folder, "photo-select"), nearby_folder, country_filter)
    if nearby is not None:
        _lineage(readiness_manifest, "readiness", {NEARBY_PLACES_FILE: nearby.places_sha256})
    elif NEARBY_PLACES_FILE in (readiness_manifest.get("inputs_sha256") or {}):
        raise PipelineError("readiness used nearby places; pass the same nearby folder")
    _lineage(
        _manifest(checks_folder, "auto-check"), "auto-check", {BLOCKS_FILE: digests[BLOCKS_FILE]}
    )
    try:
        if (
            _manifest(blocks_folder, "draft-blocks")["rules_version"]
            != _manifest(checks_folder, "auto-check")["blocks_rules_version"]
        ):
            raise PipelineError(
                "auto-check output was built from other draft-blocks rules; run auto-check again"
            )
    except (KeyError, TypeError):
        raise PipelineError("a manifest.json is damaged; run the job again") from None
    _lineage(
        _manifest(prices_folder, "price-bands"),
        "price-bands",
        {n: digests[n] for n in (LISTINGS_FILE, BLOCKS_FILE, STOPS_FILE, CHECKS_FILE)},
    )

    counts: Counter[str] = Counter()
    try:
        city_rows = [r for r in _rows(readiness_folder / READINESS_FILE) if r["published"]]
        selected = [r for r in city_rows if r["slug"]]
        if len({r["slug"] for r in selected}) != len(selected):
            raise PipelineError("two published cities share one slug in readiness")
        counts["skipped_no_slug"] = len(city_rows) - len(selected)
        wanted = {r["wikivoyage_page_id"] for r in selected}
        place_rows: list[dict[str, Any]] = []
        notice_of: dict[tuple[int, int], tuple[str, str | None, str | None]] = {}
        extras = extra_places(nearby) if nearby else []
        for r in [*_rows(listings_folder / LISTINGS_FILE), *extras]:
            if r["owner_page_id"] not in wanted:
                continue
            if r.get("attribution"):
                notice_of[(r["page_id"], r["ordinal"])] = (
                    r["attribution"],
                    r.get("licence"),
                    r.get("licence_url"),
                )
            if (
                r["kind"] in rules.place_sections
                and not r["duplicate_of"]
                and (r.get("name") or "").strip()
            ):
                place_rows.append(r)
        place_listings = _group(place_rows, "owner_page_id")
        by_subject = lambda r: (r["subject"], r["page_id"], r["ordinal"])  # noqa: E731
        photos = _unique(_rows(photos_folder / PHOTOS_FILE), by_subject, PHOTOS_FILE)
        summaries = _unique(_rows(summaries_folder / SUMMARIES_FILE), by_subject, SUMMARIES_FILE)
        timezones = _unique(
            _rows(timezones_folder / TIMEZONES_FILE),
            lambda r: r["wikivoyage_page_id"],
            TIMEZONES_FILE,
        )
        status = {
            k: r["check_status"]
            for k, r in _unique(
                _rows(checks_folder / CHECKS_FILE), lambda r: r["block_id"], CHECKS_FILE
            ).items()
        }
        block_rows = [
            b
            for b in _rows(blocks_folder / BLOCKS_FILE)
            if status.get(b["block_id"]) == "checked" and b["owner_page_id"] in wanted
        ]
        owner_of = {b["block_id"]: b["owner_page_id"] for b in block_rows}
        stops = _group(
            (s for s in _rows(blocks_folder / STOPS_FILE) if s["block_id"] in owner_of),
            "block_id",
        )
        block_costs = _unique(
            (r for r in _rows(prices_folder / COSTS_FILE) if r["block_id"] in owner_of),
            lambda r: r["block_id"],
            COSTS_FILE,
        )
        days = _group(
            (
                {**d, "owner": owner_of[d["block_ids"][0]]}
                for d in _rows(prices_folder / DAY_COSTS_FILE)
                if d["block_ids"]
                and all(b in owner_of for b in d["block_ids"])
                and len({owner_of[b] for b in d["block_ids"]}) == 1
            ),
            "owner",
        )
        bands = _group(
            (r for r in _rows(prices_folder / BANDS_FILE) if r["owner_page_id"] in wanted),
            "owner_page_id",
        )
        stop_keys = {
            s["listing_key"] for rows in stops.values() for s in rows if s.get("listing_key")
        }
        stop_listings = {
            r["listing_key"]: r
            for r in _rows(listings_folder / LISTINGS_FILE)
            if r.get("listing_key") in stop_keys
        }
        sections: dict[int, dict[str, Any]] = {}
        for r in sorted(
            (
                r
                for r in _rows(listings_folder / SECTIONS_FILE)
                if r["section_key"] == "get_around" and r["page_id"] in wanted
            ),
            key=lambda r: r["ordinal"],
        ):
            sections.setdefault(r["page_id"], r)  # a page's first "Get around" heading
    except (KeyError, TypeError, AttributeError):
        raise PipelineError("an input file has a damaged row") from None

    up = build.Upstream(
        place_listings=place_listings,
        photos=photos,
        summaries=summaries,
        timezones=timezones,
        blocks=_group(block_rows, "owner_page_id"),
        stops=stops,
        block_costs=block_costs,
        days=days,
        bands=bands,
        counts=counts,
        stop_listings=stop_listings,
        sections=sections,
    )
    files: dict[str, bytes] = {}
    index_cities: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    notices: set[tuple[str, str | None, str | None]] = set()
    for row in sorted(selected, key=lambda r: r["slug"]):
        try:
            city = build.build_city(row, up)
        except (KeyError, TypeError, AttributeError):
            raise PipelineError("an input file has a damaged row") from None
        name = f"cities/{row['slug']}.json"
        data = dump_document(CityFile, city, name)
        if len(data) > limits.max_file_bytes:
            raise PipelineError(
                f"{name} is {len(data)} bytes, over the {limits.max_file_bytes} bytes limit"
            )
        files[name] = data
        index_cities.append(
            {
                "slug": city["slug"],
                "title": city["title"],
                "country_iso": city["country_iso"],
                "tier": city["tier"],
                "hero": city["hero"],
                "file": f"{API}/{name}",
            }
        )
        subjects = [("summary", "city", city["summary"]), ("photo", "hero", city["hero"])]
        for p in city["places"]:
            subjects += [
                ("summary", f"place:{p['key']}", p["summary"]),
                ("photo", f"place:{p['key']}", p["photo"]),
            ]
        for kind, subject, item in subjects:
            if item:
                counts["photos" if kind == "photo" else "summaries"] += 1
                entries.append(
                    {
                        "kind": kind,
                        "city": city["slug"],
                        "subject": subject,
                        **{k: item["credit"].get(k) for k in CREDIT_KEYS},
                    }
                )
        if city["getting_around"]["excerpt"]:
            counts["with_getting_around_excerpt"] += 1
            entries.append(
                {
                    "kind": "getting_around",
                    "city": city["slug"],
                    "subject": "city",
                    **{k: city["getting_around"]["excerpt"]["credit"].get(k) for k in CREDIT_KEYS},
                }
            )
        counts["with_ride_apps"] += city["getting_around"]["ride_apps"] is not None
        counts["without_hero"] += city["hero"] is None
        counts["without_summary"] += city["summary"] is None
        counts["not_ready_published"] += city["tier"] == "not_ready"
        zone = timezones.get(row["wikivoyage_page_id"]) or {}
        if zone.get("timezone") and zone.get("timezone_attribution"):
            notices.add((zone["timezone_attribution"], zone.get("timezone_licence"), None))
        # Place and stop names and points come from Wikivoyage listings: credit those listings.
        kept = {p["key"] for p in city["places"]}
        used = {
            (p["page_id"], p["ordinal"])
            for p in place_listings.get(row["wikivoyage_page_id"], [])
            if p["listing_key"] in kept
        }
        for b in city["blocks"]:
            used |= {(s["page_id"], s["ordinal"]) for s in up.stops.get(b["id"], [])}
        notices |= {notice_of[k] for k in used if k in notice_of}
    files |= index_files(index_cities, limits.max_index_bytes)
    parts: list[dict[str, Any]] = []
    for chunk in _chunks(entries, limits.attribution_part_bytes):
        name = f"attributions/part-{len(parts) + 1:03d}.json"
        files[name] = dump_document(
            AttributionsPart, {"schema_version": SCHEMA_VERSION, "entries": chunk}, name
        )
        parts.append(
            {
                "file": name,
                "entries": len(chunk),
                "first_city": chunk[0]["city"],
                "last_city": chunk[-1]["city"],
            }
        )
    attributions = {
        "schema_version": SCHEMA_VERSION,
        "notices": [
            {"attribution": a, "licence": lic, "licence_url": url}
            for a, lic, url in sorted(notices, key=lambda n: tuple(x or "" for x in n))
        ],
        "parts": parts,
    }
    files["attributions.json"] = dump_document(AttributionsFile, attributions, "attributions.json")
    if len(files) + 3 > limits.max_files:  # plus manifest.json, _headers and index.html
        raise PipelineError(
            f"{len(files) + 3} files, over the {limits.max_files} files limit; "
            "city files need bundling by country"
        )

    for name, data in files.items():
        if (
            not name.startswith(("cities/", "index/")) and name != "index.json"
        ):  # index_files checks those
            if len(data) > limits.max_other_bytes:
                raise PipelineError(
                    f"{name} is {len(data)} bytes, over the {limits.max_other_bytes} bytes limit"
                )
    hashes = {n: hashlib.sha256(d).hexdigest() for n, d in sorted(files.items())}
    version = version_of(hashes)
    totals = {
        "without_hero": counts["without_hero"],
        "own_hero_rejected": {
            k.removeprefix("own_hero_rejected_"): v
            for k, v in sorted(counts.items())
            if k.startswith("own_hero_rejected_")
        },
        "with_getting_around_excerpt": counts["with_getting_around_excerpt"],
        "with_ride_apps": counts["with_ride_apps"],
        "getting_around_left_out_no_credit": counts["getting_around_left_out_no_credit"],
        "without_summary": counts["without_summary"],
        "not_ready_published": counts["not_ready_published"],
        "cities": len(index_cities),
        "skipped_no_slug": counts["skipped_no_slug"],
        "photos": counts["photos"],
        "summaries": counts["summaries"],
        "photos_left_out_no_credit": counts["photos_left_out_no_credit"],
        "photos_left_out_bad_author": counts["photos_left_out_bad_author"],
        "summaries_left_out_no_credit": counts["summaries_left_out_no_credit"],
        "largest_city_bytes": max(
            (len(d) for n, d in files.items() if n.startswith("cities/")), default=0
        ),
        "files": len(files) + 2,
        "total_bytes": sum(len(d) for d in files.values()),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "dump_date": dump_date,
        "country_filter": country_filter,
        "generated_on": today.isoformat(),
        "readiness_rules_version": readiness_manifest.get("readiness_rules_version"),
        "blocks_rules_version": readiness_manifest.get("blocks_rules_version"),
        "counts": {k: totals[k] for k in ("cities", "photos", "summaries")},
        "files": {n: {"sha256": hashes[n], "bytes": len(files[n])} for n in hashes},
    }
    files["manifest.json"] = dump_document(ManifestFile, manifest, "manifest.json")
    if len(files["manifest.json"]) > limits.max_other_bytes:
        raise PipelineError(
            f"manifest.json is {len(files['manifest.json'])} bytes, over the "
            f"{limits.max_other_bytes} bytes limit"
        )

    # Write beside the old site and swap at the end, so a failed write never destroys a good site.
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    site, staging = target / "site", target / "site.new"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        for name, data in files.items():
            path = staging / API / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / "_headers").write_bytes(HEADERS.encode("utf-8"))
        (staging / "index.html").write_bytes(LANDING_PAGE.encode("utf-8"))
        if site.exists():
            shutil.rmtree(site)
        staging.rename(site)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return PublishResult(target, site, totals)
