"""Read the stops: see/do listings with a trusted point, joined with their facts and photo state,
plus attraction sub-articles of a city (an article that is a sight, like Agra/Taj Mahal).

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
DEST_FACTS_FILE = "destination_facts.jsonl"
ARTICLES_FILE = "articles.jsonl"
SUMMARIES_FILE = "summaries.jsonl"
SLUGS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "slugs.json"


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
    listings_folder: Path,
    enrich_folder: Path,
    photos_folder: Path,
    country_filter: str | None,
    *,
    discover_folder: Path | None = None,
    summaries_folder: Path | None = None,
) -> dict[str, str]:
    hashes = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter)
    }
    names = (FACTS_FILE, DEST_FACTS_FILE) if discover_folder is not None else (FACTS_FILE,)
    _, enrich = verify_enrich_output(enrich_folder, country_filter, names)
    hashes.update(enrich)
    hashes[PHOTOS_FILE] = _verify(photos_folder, PHOTOS_FILE, "photo-select", country_filter)
    if discover_folder is not None:
        hashes[ARTICLES_FILE] = _verify_articles(discover_folder, listings_folder)
    if summaries_folder is not None:
        hashes[SUMMARIES_FILE] = _verify(
            summaries_folder, SUMMARIES_FILE, "wikipedia-summary", country_filter
        )
    return hashes


def _verify_articles(discover_folder: Path, listings_folder: Path) -> str:
    """articles.jsonl must be the very file fetch-listings read (its manifest records the hash;
    a country run reads the world discover folder)."""
    path = discover_folder / ARTICLES_FILE
    if not path.is_file():
        raise PipelineError(f"{ARTICLES_FILE} not found in {discover_folder}; run discover first")
    try:
        manifest = json.loads((listings_folder / "manifest.json").read_text(encoding="utf-8"))
        recorded = manifest["articles_sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError("the fetch-listings manifest.json is damaged; run it again") from None
    digest = _sha256(path)
    if digest != recorded:
        raise PipelineError(f"{ARTICLES_FILE} does not match the fetch-listings manifest")
    return digest


def _read_slugs(path: Path) -> dict[int, tuple[str, str]]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
        return {int(k): (v["slug"], v["title"]) for k, v in entries.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise PipelineError(f"{path.name} could not be read; it is the slug registry") from None


def _subarticle_stops(
    discover_folder: Path,
    enrich_folder: Path,
    country_filter: str | None,
    rules: BlockRules,
    listing_stops: list[Stop],
    owners: dict[int, tuple[str, str]],
    slugs_path: Path,
    with_photo: set[tuple],
    with_summary: set[tuple],
    excluded: Counter,
) -> list[Stop]:
    """Sights that Wikivoyage keeps as their own article under a city (Agra/Taj Mahal)."""
    rule = rules.subarticles
    try:
        articles = {r["wikivoyage_page_id"]: r for r in _rows(discover_folder / ARTICLES_FILE)}
        dests = list(_rows(enrich_folder / DEST_FACTS_FILE))
    except (KeyError, TypeError):
        raise PipelineError(f"{ARTICLES_FILE} or {DEST_FACTS_FILE} has a damaged row") from None
    by_title = {a["title"]: a for a in articles.values() if isinstance(a.get("title"), str)}
    listed: set[tuple] = {(s.owner_id, s.name.casefold()) for s in listing_stops}
    listed |= {(s.owner_id, s.wikidata) for s in listing_stops if s.wikidata}
    slugs: dict[int, tuple[str, str]] | None = None
    found: list[Stop] = []
    for fact in sorted(dests, key=lambda r: r.get("wikivoyage_page_id") or 0):
        try:
            article = articles.get(fact["wikivoyage_page_id"])
            if article is None or not article.get("parent"):
                continue
            if country_filter and article.get("tree_country_iso") != country_filter:
                continue
            parent = by_title.get(article["parent"])
            if parent is None or parent.get("type") not in rule.parent_types:
                continue
            page_id = article["wikivoyage_page_id"]
            owner_id = parent["wikivoyage_page_id"]
            if article.get("type") in rule.skip_types:
                excluded["subarticle_type_skipped"] += 1
                continue
            if fact.get("kind") not in rule.kinds:
                excluded["subarticle_no_attraction_kind"] += 1
                continue
            if rules.blocked_flags & set(fact.get("flags") or []):
                excluded["subarticle_coord_disputed"] += 1
                continue
            point = _point(fact.get("coord_lat"), fact.get("coord_long"))
            if point is None:
                excluded["subarticle_no_coordinates"] += 1
                continue
            title = article["title"]
            name = title.removeprefix(parent["title"] + "/").strip()
            wikidata = fact.get("wikidata_id") or None
            if (owner_id, name.casefold()) in listed or (
                wikidata and (owner_id, wikidata) in listed
            ):
                excluded["subarticle_already_listed"] += 1
                continue
            if owner_id not in owners:
                slugs = slugs if slugs is not None else _read_slugs(slugs_path)
                if owner_id not in slugs:
                    excluded["subarticle_parent_has_no_slug"] += 1
                    continue
                owners[owner_id] = slugs[owner_id]
            slug, owner_title = owners[owner_id]
            found.append(
                Stop(
                    key=f"article:{page_id}",
                    page_id=page_id,
                    ordinal=0,
                    owner_id=owner_id,
                    owner_slug=slug,
                    owner_title=owner_title,
                    name=name,
                    lat=point[0],
                    lon=point[1],
                    section="see",
                    kind=fact["kind"],
                    unesco=bool(fact.get("is_unesco")),
                    has_photo=("article", page_id, 0) in with_photo,
                    hours=None,
                    source="article",
                    has_summary=("article", page_id, 0) in with_summary,
                    wikidata=wikidata or "",
                )
            )
        except (KeyError, TypeError, AttributeError, ValueError):
            raise PipelineError(f"{DEST_FACTS_FILE} has a damaged row") from None
    return found


def read_stops(
    listings_folder: Path,
    enrich_folder: Path,
    photos_folder: Path,
    country_filter: str | None,
    rules: BlockRules,
    *,
    discover_folder: Path | None = None,
    summaries_folder: Path | None = None,
    slugs_path: Path = SLUGS_PATH,
) -> tuple[list[Stop], dict[str, int]]:
    """The usable stops (sorted by owner, page, ordinal) and a count of the rest by reason.
    With `discover_folder`, attraction sub-articles of a city are stops too."""
    try:
        facts = {(r["page_id"], r["ordinal"]): r for r in _rows(enrich_folder / FACTS_FILE)}
    except (KeyError, TypeError):
        raise PipelineError(f"{FACTS_FILE} has a damaged row") from None
    try:
        with_photo = {
            (r["subject"], r["page_id"], r["ordinal"])
            for r in _rows(photos_folder / PHOTOS_FILE)
            if r.get("photo")
        }
    except (KeyError, TypeError):
        raise PipelineError(f"{PHOTOS_FILE} has a damaged row") from None
    with_summary: set[tuple] = set()
    if summaries_folder is not None:
        try:
            with_summary = {
                (r["subject"], r["page_id"], r["ordinal"])
                for r in _rows(summaries_folder / SUMMARIES_FILE)
                if r.get("summary")
            }
        except (KeyError, TypeError):
            raise PipelineError(f"{SUMMARIES_FILE} has a damaged row") from None
    stops: list[Stop] = []
    owners: dict[int, tuple[str, str]] = {}
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
                owner_slug = row["owner_slug"] or str(row["owner_page_id"])
                owners.setdefault(row["owner_page_id"], (owner_slug, row["owner_title"] or ""))
                stops.append(
                    Stop(
                        key=row["listing_key"],
                        page_id=row["page_id"],
                        ordinal=row["ordinal"],
                        owner_id=row["owner_page_id"],
                        owner_slug=owner_slug,
                        owner_title=row["owner_title"] or "",
                        name=name,
                        lat=point[0],
                        lon=point[1],
                        section=row["kind"],
                        kind=(fact or {}).get("kind"),
                        unesco=bool((fact or {}).get("is_unesco")),
                        has_photo=("listing", *key) in with_photo,
                        hours=_real_hours(row.get("hours_parsed")),
                        has_summary=("listing", *key) in with_summary,
                        wikidata=str((fact or {}).get("wikidata_id") or ""),
                    )
                )
        except (KeyError, TypeError, AttributeError, ValueError):
            raise PipelineError("listings.jsonl has a damaged row") from None
    if discover_folder is not None:
        stops += _subarticle_stops(
            discover_folder,
            enrich_folder,
            country_filter,
            rules,
            stops,
            owners,
            slugs_path,
            with_photo,
            with_summary,
            excluded,
        )
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
