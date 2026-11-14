"""Read the stops: see/do listings with a trusted point, joined with their facts and photo state.

The three input folders are hash-checked against their own manifests, so a stale or edited file is
refused. A listing that cannot be used is counted under the first reason that applies; nothing is
filled in or guessed. Error messages name files only (the logs are public).
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from catalogue.blocks.model import Stop
from catalogue.blocks.rules import BlockRules
from catalogue.enrich.inputs import _rows, _sha256, verify_enrich_output
from catalogue.errors import PipelineError

LISTINGS_FILE = "listings.jsonl"
FACTS_FILE = "listing_facts.jsonl"
PHOTOS_FILE = "photo_selection.jsonl"


def _verify(folder: Path, name: str, job: str, country_filter: str | None) -> str:
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"manifest.json not found in {folder}; run {job} first")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded_country = manifest["country_filter"]
        recorded = manifest["outputs_sha256"][name]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(f"the {job} manifest.json is damaged; run {job} again") from None
    if recorded_country != country_filter:
        raise PipelineError(f"{job} was run for a different country filter; use the same --country")
    path = folder / name
    if not path.is_file():
        raise PipelineError(f"{name} not found in {folder}")
    digest = _sha256(path)
    if digest != recorded:
        raise PipelineError(f"{name} does not match the {job} manifest")
    return digest


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _point(lat: Any, lon: Any) -> tuple[float, float] | None:
    la, lo = _number(lat), _number(lon)
    if la is None or lo is None or not -90 <= la <= 90 or not -180 <= lo <= 180:
        return None
    if la == 0 and lo == 0:
        return None  # null island is an empty value, not a place
    return la, lo


_CLOCK = re.compile(r"([01]?[0-9]|2[0-4]):([0-5][0-9])")


def _clock_ok(value: Any) -> bool:
    match = _CLOCK.fullmatch(value) if isinstance(value, str) else None
    return match is not None and (match.group(1) != "24" or match.group(2) == "00")


def _real_hours(parsed: Any) -> dict[str, Any] | None:
    """Parsed hours count as known only with a well-formed schedule (or open all day); anything
    else, including a range that cannot be read or has equal open and close, is unknown."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("always_open") is True:
        return parsed
    schedule = parsed.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        return None
    for entry in schedule:
        ranges = entry.get("ranges") if isinstance(entry, dict) else None
        if not isinstance(ranges, list) or not ranges:
            return None
        for item in ranges:
            if not (isinstance(item, list) and len(item) == 2 and all(map(_clock_ok, item))):
                return None
            if item[0] == item[1]:
                return None
    return parsed


def input_hashes(
    listings_folder: Path, enrich_folder: Path, photos_folder: Path, country_filter: str | None
) -> dict[str, str]:
    hashes = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter)
    }
    _, enrich = verify_enrich_output(enrich_folder, country_filter, (FACTS_FILE,))
    hashes.update(enrich)
    hashes[PHOTOS_FILE] = _verify(photos_folder, PHOTOS_FILE, "photo-select", country_filter)
    return hashes


def read_stops(
    listings_folder: Path,
    enrich_folder: Path,
    photos_folder: Path,
    country_filter: str | None,
    rules: BlockRules,
) -> tuple[list[Stop], dict[str, int]]:
    """The usable stops (sorted by owner, page, ordinal) and a count of the rest by reason."""
    try:
        facts = {(r["page_id"], r["ordinal"]): r for r in _rows(enrich_folder / FACTS_FILE)}
    except (KeyError, TypeError):
        raise PipelineError(f"{FACTS_FILE} has a damaged row") from None
    try:
        with_photo = {
            (r["page_id"], r["ordinal"])
            for r in _rows(photos_folder / PHOTOS_FILE)
            if r.get("subject") == "listing" and r.get("photo")
        }
    except (KeyError, TypeError):
        raise PipelineError(f"{PHOTOS_FILE} has a damaged row") from None
    stops: list[Stop] = []
    excluded: Counter = Counter()
    for row in _rows(listings_folder / LISTINGS_FILE):
        try:
            key = (row["page_id"], row["ordinal"])
            if not all(_whole(v) for v in (*key, row["owner_page_id"])):
                raise TypeError
            name = (row["name"] or "").strip()
            fact = facts.get(key)
            if row["kind"] not in rules.sections:
                excluded["section_not_planned"] += 1
            elif row["owner_type"] not in rules.owner_types:
                excluded["owner_type_not_planned"] += 1
            elif row["duplicate_of"]:
                excluded["duplicate"] += 1
            elif not name:
                excluded["no_name"] += 1
            elif rules.blocked_flags & (
                set((fact or {}).get("flags") or []) | set(row["quality_flags"] or [])
            ):
                excluded["coord_disputed"] += 1
            else:
                if fact is not None:
                    point = _point(fact.get("coord_lat"), fact.get("coord_long"))
                elif "coord_invalid" in (row["quality_flags"] or []):
                    point = None
                else:
                    point = _point(row["lat"], row["long"])
                if point is None:
                    excluded["no_coordinates"] += 1
                    continue
                stops.append(
                    Stop(
                        key=row["listing_key"],
                        page_id=row["page_id"],
                        ordinal=row["ordinal"],
                        owner_id=row["owner_page_id"],
                        owner_slug=row["owner_slug"] or str(row["owner_page_id"]),
                        owner_title=row["owner_title"] or "",
                        name=name,
                        lat=point[0],
                        lon=point[1],
                        section=row["kind"],
                        kind=(fact or {}).get("kind"),
                        unesco=bool((fact or {}).get("is_unesco")),
                        has_photo=key in with_photo,
                        hours=_real_hours(row.get("hours_parsed")),
                    )
                )
        except (KeyError, TypeError, AttributeError, ValueError):
            raise PipelineError("listings.jsonl has a damaged row") from None
    names_at: dict[tuple, set[str]] = {}
    for s in stops:
        names_at.setdefault((s.owner_id, round(s.lat, 6), round(s.lon, 6)), set()).add(
            s.name.casefold()
        )
    shared = {k for k, names in names_at.items() if len(names) >= rules.shared_coordinate_names}
    if shared:
        kept = [s for s in stops if (s.owner_id, round(s.lat, 6), round(s.lon, 6)) not in shared]
        excluded["coord_shared"] += len(stops) - len(kept)
        stops = kept
    stops.sort(key=lambda s: (s.owner_id, s.page_id, s.ordinal))
    return stops, dict(excluded)
