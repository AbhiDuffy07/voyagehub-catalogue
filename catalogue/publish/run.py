"""`publish-static` (B13): the static JSON site for the published cities.

Reads the hash-checked outputs of earlier jobs for one dump and country filter, and writes
`out/publish/<date>[-CC]/site/` (the folder that is deployed): `v1/cities/<slug>.json`,
`v1/index.json`, `v1/attributions.json` (credits in `v1/attributions/part-NNN.json`),
`v1/manifest.json` (version and hashes) and `_headers`. A city is written only when readiness
says published and it has a slug. Every file is validated against its pydantic model and
size-checked before anything is written. Offline and deterministic. Error messages name files only
(the logs are public).
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

from pydantic import BaseModel, ValidationError

from catalogue.blocks.inputs import (
    LISTINGS_FILE,
    PHOTOS_FILE,
    SUMMARIES_FILE,
    _verify,
)
from catalogue.enrich.inputs import _rows
from catalogue.errors import PipelineError
from catalogue.listings.run import parse_dump_date
from catalogue.publish import build
from catalogue.publish.limits import PublishLimits, load_limits
from catalogue.publish.models import (
    SCHEMA_VERSION,
    AttributionsFile,
    AttributionsPart,
    CityFile,
    IndexFile,
    ManifestFile,
)
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
BANDS_FILE = "city_price_bands.jsonl"
COSTS_FILE = "block_costs.jsonl"
DAY_COSTS_FILE = "day_costs.jsonl"
API = "v1"
HEADERS = """/v1/index.json
  Cache-Control: public, max-age=300
/v1/manifest.json
  Cache-Control: public, max-age=60
/v1/attributions.json
  Cache-Control: public, max-age=3600
/v1/cities/*
  Cache-Control: public, max-age=3600
"""
CREDIT_KEYS = ("author", "licence", "licence_url", "source_url", "attribution")


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
                f"photos {t['photos']}, summaries {t['summaries']}; left out for missing credit: "
                f"{t['photos_left_out_no_credit']} photos, "
                f"{t['summaries_left_out_no_credit']} summaries",
            ]
        )


def _dump(model: type[BaseModel], document: dict[str, Any], name: str) -> bytes:
    try:
        model.model_validate(document)
    except ValidationError:
        raise PipelineError(f"{name} does not match its schema") from None
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


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
        length = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":"))) + 1
        if not parts or size + length > target:
            parts.append([])
            size = 0
        parts[-1].append(entry)
        size += length
    return parts


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
    _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter)
    _verify(summaries_folder, SUMMARIES_FILE, "wikipedia-summary", country_filter)
    _verify(photos_folder, PHOTOS_FILE, "photo-select", country_filter)
    _verify(blocks_folder, BLOCKS_FILE, "draft-blocks", country_filter)
    _verify(blocks_folder, STOPS_FILE, "draft-blocks", country_filter)
    _verify(checks_folder, CHECKS_FILE, "auto-check", country_filter)
    _verify_timezones(timezones_folder, country_filter)
    for name in (BANDS_FILE, COSTS_FILE, DAY_COSTS_FILE):
        _verify(prices_folder, name, "price-bands", country_filter)

    counts: Counter[str] = Counter()
    try:
        city_rows = [r for r in _rows(readiness_folder / READINESS_FILE) if r["published"]]
        selected = [r for r in city_rows if r["slug"]]
        counts["skipped_no_slug"] = len(city_rows) - len(selected)
        wanted = {r["wikivoyage_page_id"] for r in selected}
        place_listings = _group(
            (
                r
                for r in _rows(listings_folder / LISTINGS_FILE)
                if r["owner_page_id"] in wanted
                and r["kind"] in rules.place_sections
                and not r["duplicate_of"]
                and (r.get("name") or "").strip()
            ),
            "owner_page_id",
        )
        photos = {
            (r["subject"], r["page_id"], r["ordinal"]): r
            for r in _rows(photos_folder / PHOTOS_FILE)
        }
        summaries = {
            (r["subject"], r["page_id"], r["ordinal"]): r
            for r in _rows(summaries_folder / SUMMARIES_FILE)
        }
        timezones = {r["wikivoyage_page_id"]: r for r in _rows(timezones_folder / TIMEZONES_FILE)}
        status = {r["block_id"]: r["check_status"] for r in _rows(checks_folder / CHECKS_FILE)}
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
        block_costs = {
            r["block_id"]: r for r in _rows(prices_folder / COSTS_FILE) if r["block_id"] in owner_of
        }
        days = _group(
            (
                {**d, "owner": owner_of[d["block_ids"][0]]}
                for d in _rows(prices_folder / DAY_COSTS_FILE)
                if d["block_ids"] and all(b in owner_of for b in d["block_ids"])
            ),
            "owner",
        )
        bands = _group(
            (r for r in _rows(prices_folder / BANDS_FILE) if r["owner_page_id"] in wanted),
            "owner_page_id",
        )
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
        data = _dump(CityFile, city, name)
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
                        **{k: item["credit"][k] for k in CREDIT_KEYS},
                    }
                )
        zone = timezones.get(row["wikivoyage_page_id"]) or {}
        if zone.get("timezone") and zone.get("timezone_attribution"):
            notices.add((zone["timezone_attribution"], zone.get("timezone_licence"), None))
        for listing in place_listings.get(row["wikivoyage_page_id"], []):
            if listing.get("attribution"):
                notices.add(
                    (listing["attribution"], listing.get("licence"), listing.get("licence_url"))
                )
    index = {"schema_version": SCHEMA_VERSION, "cities": index_cities}
    files["index.json"] = _dump(IndexFile, index, "index.json")
    if len(files["index.json"]) > limits.max_index_bytes:
        raise PipelineError(
            f"index.json is {len(files['index.json'])} bytes, over the {limits.max_index_bytes} "
            "bytes limit; it needs sharding by country"
        )
    parts: list[dict[str, Any]] = []
    for chunk in _chunks(entries, limits.attribution_part_bytes):
        name = f"attributions/part-{len(parts) + 1:03d}.json"
        files[name] = _dump(
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
    files["attributions.json"] = _dump(AttributionsFile, attributions, "attributions.json")
    if len(files) + 2 > limits.max_files:  # plus manifest.json and _headers
        raise PipelineError(
            f"{len(files) + 2} files, over the {limits.max_files} files limit; "
            "city files need bundling by country"
        )

    hashes = {n: hashlib.sha256(d).hexdigest() for n, d in sorted(files.items())}
    version = hashlib.sha256("".join(f"{n}:{h}\n" for n, h in hashes.items()).encode()).hexdigest()
    totals = {
        "cities": len(index_cities),
        "skipped_no_slug": counts["skipped_no_slug"],
        "photos": counts["photos"],
        "summaries": counts["summaries"],
        "photos_left_out_no_credit": counts["photos_left_out_no_credit"],
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
    files["manifest.json"] = _dump(ManifestFile, manifest, "manifest.json")

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    site = target / "site"
    if site.exists():
        shutil.rmtree(site)
    for name, data in files.items():
        path = site / API / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (site / "_headers").write_bytes(HEADERS.encode("utf-8"))
    return PublishResult(target, site, totals)
