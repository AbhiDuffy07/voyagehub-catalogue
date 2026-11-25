"""`nearby` (B18): more photographed places for the cities that are short of them, from open data.

A city is short when it has fewer photographed places than readiness asks for
(`min_photographed_places`, counted exactly as readiness counts them, on a first photo-select
pass). For each short city, in this order:

1. Wikidata items near the city centre (GeoData search, `[places] radius_km`) become extra places
   when they have an English label, a point, one image that passes every photo check, a mapped
   visitor kind, the city's own country, and when they are nearer this city's centre than any other
   city's. Items that are Wikivoyage destinations, already a listing (same item, or the same name
   and place), or the same place as an earlier extra place are skipped. Prices and hours are never
   filled: an extra place has none, and flags say so.
2. Every see/do listing of the city that has a trusted point but no photo is searched on Commons
   (GeoData, `[geosearch] search_radius_m`). Files that name the place, or unambiguous files very
   close to it, become photo candidates; see `pick.accept_hits`.

Each candidate keeps B5's candidate shape (source, licence, author, attribution) plus the distance
and the place name, so the second photo-select pass treats it like any other candidate. Requests go
through `api_get` (maxlag, back-off, retries) and the polite client (User-Agent, 3 at a time, 200 a
minute), and replies are cached under `out/cache/`. Writes `out/nearby/<date>[-CC]/`. Logs and the
report hold counts only.
"""

import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.blocks.inputs import ARTICLES_FILE, LISTINGS_FILE, _point, _verify, _verify_articles
from catalogue.blocks.rules import BlockRules, load_block_rules
from catalogue.discover.output import write_manifest, write_text
from catalogue.enrich.claims import ItemFacts, parse_item
from catalogue.enrich.facts import kind_of
from catalogue.enrich.inputs import _rows, verify_enrich_output
from catalogue.enrich.items import WIKIDATA_API
from catalogue.enrich.kindmap import load_kind_map
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.listings.keys import key_slug
from catalogue.listings.output import JsonlWriter
from catalogue.listings.quality import distance_km
from catalogue.listings.run import parse_dump_date
from catalogue.nearby.cache import ApiCache
from catalogue.nearby.inputs import CANDIDATES_FILE, PLACES_FILE
from catalogue.nearby.pick import (
    CentreIndex,
    GeoHit,
    KnownPlace,
    OtherPlace,
    accept_hits,
    photo_problem,
    place_words,
    same_place,
)
from catalogue.nearby.rules import NearbyRules, load_nearby_rules
from catalogue.photos.commons import (
    BUSY_CODE,
    BUSY_FIRST_WAIT,
    BUSY_GIVE_UP,
    BUSY_RETRIES,
    COMMONS_API,
    FileInfo,
    _run_bounded,
    fetch_file_info,
)
from catalogue.photos.rules import PhotoRules, load_photo_rules
from catalogue.photos.run import INPUT_FILES, Candidate, Subject
from catalogue.photos.run import _row as candidate_row
from catalogue.photos.select import SelectionRules, load_selection_rules
from catalogue.photos.select_run import PHOTO_FIELDS, _verify_photos
from catalogue.publish.build import photo_of
from catalogue.publish.hero import HeroRules, load_hero_rules
from catalogue.readiness.rules import ReadinessRules, load_readiness_rules, place_owners

SCHEMA_VERSION = 1
SELECTION_FILE = "photo_selection.jsonl"
OUTPUT_FILES = (PLACES_FILE, CANDIDATES_FILE, "report.md")
# Extra places live in the owning city's page with ordinals far above any Wikivoyage ordinal, so
# (page_id, ordinal) stays one key across listings and extra places. The ordinal is the base plus
# the item number, so it does not move when other places come or go.
EXTRA_ORDINAL_BASE = 1_000_000_000
WIKIDATA_LICENCE = "CC0 1.0"
WIKIDATA_LICENCE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
WIKIDATA_ATTRIBUTION = "Wikidata contributors"
ENTITY_BATCH = 50
# GeoData searches run on the CirrusSearch backend, which limits searches per client and answers
# `cirrussearch-too-busy-error` beyond that: one search at a time, like B5's category search.
SEARCH_WORKERS = 1


@dataclass(frozen=True, slots=True)
class NearbyResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        return "\n".join(
            [
                f"cities assessed: {t['cities']}; short of photographed places: "
                f"{t['short_cities']}",
                f"extra places: {t['extra_places']} in {t['cities_with_extra_places']} cities",
                (
                    f"places searched on Commons: {t['subjects']}; given a photo candidate: "
                    f"{t['subjects_with_candidate']}"
                    if t["geosearch_enabled"]
                    else "Commons geosearch: off"
                ),
                f"requests: {t['cache']['misses']} sent, {t['cache']['hits']} from the cache",
            ]
        )


@dataclass(frozen=True, slots=True)
class Place:
    """A listing of a short city, as `nearby` sees it."""

    page_id: int
    ordinal: int
    owner: int
    name: str
    kind: str
    listing_key: str
    country_iso: str | None
    qid: str | None
    point: tuple[float, float] | None


# --- inputs -------------------------------------------------------------------------------------


def photographed_places(
    owners: Mapping[tuple[int, int], int],
    selection_rows: Iterable[Mapping[str, Any]],
    hero: HeroRules,
) -> tuple[Counter, set[tuple[int, int]]]:
    """Photographed places per city, exactly as readiness counts them, and the places counted."""
    counts: Counter = Counter()
    done: set[tuple[int, int]] = set()
    for r in selection_rows:
        if not r.get("photo") or r["subject"] == "article":
            continue
        key = (r["page_id"], r["ordinal"])
        owner = owners.get(key)
        if owner is not None and photo_of(dict(r), Counter(), hero) is not None:
            counts[owner] += 1
            done.add(key)
    return counts, done


def _listing_point(
    row: Mapping[str, Any], fact: Mapping[str, Any] | None, blocks: BlockRules
) -> tuple[str, tuple[float, float] | None]:
    """The point draft-blocks would trust for a listing, or why there is none."""
    flags = set((fact or {}).get("flags") or []) | set(row.get("quality_flags") or [])
    if blocks.blocked_flags & flags:
        return "coord_disputed", None
    if fact is not None:
        point = _point(fact.get("coord_lat"), fact.get("coord_long"))
    elif "coord_invalid" in flags:
        point = None
    else:
        point = _point(row.get("lat"), row.get("long"))
    return ("ok", point) if point else ("no_point", None)


# --- requests -----------------------------------------------------------------------------------


class _Searcher:
    """GeoData searches through the cache, one at a time. A "too busy" answer is waited out
    (5, 10, 20, 40, 80 s); a search still busy after that is skipped (None) and counted, and after
    BUSY_GIVE_UP busy searches in a row on one host the rest on that host are skipped too. Nothing
    busy is cached, so a rerun fills the gaps."""

    def __init__(self, cache: ApiCache, client: PoliteClient, sleep: Callable[[float], None]):
        self._cache = cache
        self._client = client
        self._sleep = sleep
        self._streak: Counter = Counter()
        self.busy = 0

    def __call__(
        self,
        url: str,
        point: tuple[float, float],
        radius_m: int,
        limit: int,
        extra: Mapping[str, str | int],
    ) -> list[dict[str, Any]] | None:
        params = {
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{point[0]:.6f}|{point[1]:.6f}",
            "gsradius": radius_m,
            "gslimit": limit,
            **extra,
        }
        if self._streak[url] >= BUSY_GIVE_UP:
            self.busy += 1
            return None
        for attempt in range(BUSY_RETRIES + 1):
            try:
                reply = self._cache.get(self._client, url, params)
                break
            except PipelineError as exc:
                if BUSY_CODE not in str(exc):
                    raise
                if attempt == BUSY_RETRIES:
                    self._streak[url] += 1
                    self.busy += 1
                    return None
                wait = BUSY_FIRST_WAIT * 2**attempt
                host = url.split("/")[2]
                print(f"{host} search is too busy; waiting {wait:.0f} s (retry {attempt + 1})")
                self._sleep(wait)
        self._streak[url] = 0
        hits = reply.get("query", {}).get("geosearch")
        if not isinstance(hits, list):
            raise PipelineError(
                f"{url.split('/')[2]} answered a geosearch with an unexpected shape"
            )
        return [h for h in hits if isinstance(h, dict)]


def _hit_point(hit: Mapping[str, Any]) -> tuple[float, float] | None:
    return _point(hit.get("lat"), hit.get("lon"))


def _fetch_entities(
    cache: ApiCache, client: PoliteClient, qids: Iterable[str]
) -> dict[str, ItemFacts]:
    wanted = sorted(set(qids), key=lambda q: int(q[1:]))
    batches = [wanted[i : i + ENTITY_BATCH] for i in range(0, len(wanted), ENTITY_BATCH)]
    items: dict[str, ItemFacts] = {}

    def read(batch: list[str]) -> dict[str, Any]:
        return cache.get(
            client,
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "claims|sitelinks|labels",
                "languages": "en",
                "sitefilter": "enwiki",
            },
        )

    for batch, reply in _run_bounded(batches, read, "Wikidata items near cities"):
        entities = reply.get("entities")
        if not isinstance(entities, dict) or not set(batch) <= set(entities):
            raise PipelineError("Wikidata's reply did not include every requested item")
        for requested in batch:
            entity = entities[requested]
            # A merged item answers under its new ID; it is used only under the ID it really has.
            if "missing" not in entity and entity.get("id", requested) == requested:
                items[requested] = parse_item(requested, entity)
    return items


# --- rows ---------------------------------------------------------------------------------------


def _photo_shape(row: Mapping[str, Any]) -> dict[str, Any]:
    return {f: row.get(f) for f in PHOTO_FIELDS}


def _extra_row(
    city: Mapping[str, Any],
    qid: str,
    item: ItemFacts,
    place_kind: str,
    centre_km: float,
    rules: NearbyRules,
    dump_date: str,
    read_at: str,
) -> dict[str, Any]:
    assert item.coordinate is not None and item.label  # noqa: S101 - checked by the caller
    pid = city["wikivoyage_page_id"]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "wikidata_nearby",
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "revision_id": item.revision_id,
        "licence": WIKIDATA_LICENCE,
        "licence_url": WIKIDATA_LICENCE_URL,
        "attribution": WIKIDATA_ATTRIBUTION,
        "page_id": pid,
        "ordinal": EXTRA_ORDINAL_BASE + int(qid[1:]),
        "owner_page_id": pid,
        "owner_title": city.get("title"),
        "owner_type": city.get("type"),
        "country_iso": city.get("tree_country_iso"),
        "kind": "see",
        "place_kind": place_kind,
        "name": item.label,
        "listing_key": f"see:{key_slug(item.label)}~{qid}",
        "wikidata": qid,
        "enwiki_title": item.enwiki_title,
        "lat": str(item.coordinate.lat),
        "long": str(item.coordinate.lon),
        "distance_from_centre_km": round(centre_km, 3),
        "duplicate_of": None,
        "from_marker": False,
        # Never guessed: an extra place has no price or hours from any source.
        "price": "",
        "price_entries": [],
        "price_is_free": False,
        "price_status": "empty",
        "price_tier": None,
        "price_reason": None,
        "hours": "",
        "hours_parsed": None,
        "hours_status": "empty",
        "quality_flags": ["hours_unknown", "price_unknown", "wikidata_nearby"],
        "nearby_rules_version": rules.version,
        "dump_date": f"{dump_date[:4]}-{dump_date[4:6]}-{dump_date[6:]}",
        "read_at": read_at,
    }


# --- report -------------------------------------------------------------------------------------


def _counts(title: str, counts: Mapping[str, int]) -> list[str]:
    lines = [f"### {title}", ""]
    lines += [f"- `{k}`: {v}" for k, v in sorted(counts.items())] or ["- none"]
    return [*lines, ""]


def _report(totals: Mapping[str, Any], dump_date: str, rules: NearbyRules) -> str:
    return "\n".join(
        [
            f"# nearby, dump {dump_date}",
            "",
            f"- cities assessed: {totals['cities']}; short of photographed places (fewer than "
            f"{totals['min_photographed_places']}, as readiness counts them): "
            f"{totals['short_cities']}",
            f"- extra places from Wikidata: {totals['extra_places']} in "
            f"{totals['cities_with_extra_places']} cities (radius {rules.places_radius_km:g} km, "
            f"at most {rules.max_per_city} per city)",
            (
                f"- places searched on Commons: {totals['subjects']} (radius "
                f"{rules.search_radius_m} m, name-free band {rules.name_free_radius_m} m); given "
                f"a photo candidate: {totals['subjects_with_candidate']}"
                if rules.geosearch_enabled
                else "- Commons geosearch: off (config/nearby.toml [geosearch] enabled)"
            ),
            f"- candidate rows: {totals['candidate_rows']}",
            f"- searches skipped because the search service stayed too busy: "
            f"{totals['searches_busy']} (run again to fill them; the cache keeps the rest)",
            f"- requests sent: {totals['cache']['misses']}; answered from the cache: "
            f"{totals['cache']['hits']}",
            f"- rules version: {rules.version}",
            "",
            "## Why things were skipped",
            "",
            *_counts("Wikidata items", totals["places_skipped"]),
            *_counts("Places not searched", totals["subjects_skipped"]),
            *_counts("Commons files (before the file checks)", totals["geosearch_skipped"]),
            *_counts("Files that failed a photo check", totals["photos_failed"]),
            "Prices and hours of extra places are never filled; they are flagged unknown.",
            "",
        ]
    )


# --- the job ------------------------------------------------------------------------------------


def run_nearby(
    client: PoliteClient,
    *,
    discover_folder: Path,
    listings_folder: Path,
    enrich_folder: Path,
    photos_folder: Path,
    selection_folder: Path,
    out_dir: Path,
    cache_dir: Path | None,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: NearbyRules | None = None,
    readiness: ReadinessRules | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> NearbyResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_nearby_rules()
    readiness = readiness if readiness is not None else load_readiness_rules()
    photo_rules: PhotoRules = load_photo_rules()
    selection: SelectionRules = load_selection_rules()
    hero: HeroRules = load_hero_rules()
    blocks: BlockRules = load_block_rules()
    kind_map = load_kind_map()
    country_filter = country_iso.upper() if country_iso else None

    inputs = {
        LISTINGS_FILE: _verify(listings_folder, LISTINGS_FILE, "fetch-listings", country_filter),
        ARTICLES_FILE: _verify_articles(discover_folder, listings_folder),
        SELECTION_FILE: _verify(selection_folder, SELECTION_FILE, "photo-select", country_filter),
    }
    candidates_manifest, recorded_enrich, _ = _verify_photos(photos_folder, country_filter)
    _, enrich_hashes = verify_enrich_output(enrich_folder, country_filter, INPUT_FILES)
    if enrich_hashes != recorded_enrich:
        raise PipelineError(
            "the enrich-wikidata output is not the one photo-candidates read; "
            "run photo-candidates again"
        )
    inputs |= enrich_hashes
    try:
        selection_manifest = json.loads(
            (selection_folder / "manifest.json").read_text(encoding="utf-8")
        )
        if selection_manifest["photo_candidates_manifest_sha256"] != candidates_manifest:
            raise PipelineError(
                "photo-select was run on other photo candidates; run photo-select again"
            )
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError("the photo-select manifest.json is damaged; run it again") from None

    try:
        cities = {
            r["wikivoyage_page_id"]: r
            for r in _rows(discover_folder / ARTICLES_FILE)
            if r.get("type") in readiness.eligible_types
            and (country_filter is None or r.get("tree_country_iso") == country_filter)
        }
        destinations = {
            r["wikivoyage_page_id"]: r for r in _rows(enrich_folder / "destination_facts.jsonl")
        }
        facts = {
            (r["page_id"], r["ordinal"]): r for r in _rows(enrich_folder / "listing_facts.jsonl")
        }
        listings = list(_rows(listings_folder / LISTINGS_FILE))
        owners = place_owners(listings, readiness.place_sections)
        counts, photographed = photographed_places(
            owners, _rows(selection_folder / SELECTION_FILE), hero
        )
        used_files = {
            str(r["photo"]["commons_file"])
            for r in _rows(selection_folder / SELECTION_FILE)
            if r.get("photo")
        }
    except (KeyError, TypeError):
        raise PipelineError("an input file has a damaged row") from None
    if any(r["ordinal"] >= EXTRA_ORDINAL_BASE for r in listings):
        raise PipelineError("a listing ordinal reaches the range kept for extra places")
    short = {pid for pid in cities if counts[pid] < readiness.min_photographed_places}

    # Every point a city's own listings have (all sections: a photo near a hotel may show it).
    subjects_skipped: Counter = Counter()
    places_by_city: dict[int, list[Place]] = defaultdict(list)
    point_status: dict[tuple[int, int], str] = {}
    for r in listings:
        owner = r["owner_page_id"]
        # A duplicate is the same place listed twice: it is neither a subject nor "another place"
        # (it would make every photo of its original look ambiguous).
        if owner not in short or r["duplicate_of"]:
            continue
        key = (r["page_id"], r["ordinal"])
        fact = facts.get(key)
        status, point = _listing_point(r, fact, blocks)
        point_status[key] = status
        qid = (fact or {}).get("wikidata_id") or (r.get("wikidata") or "").strip() or None
        places_by_city[owner].append(
            Place(
                r["page_id"],
                r["ordinal"],
                owner,
                (r.get("name") or "").strip(),
                r["kind"],
                r["listing_key"],
                r.get("country_iso"),
                qid,
                point,
            )
        )
    shared: set[tuple[int, int]] = set()
    for places in places_by_city.values():
        names_at: dict[tuple[float, float], set[str]] = defaultdict(set)
        for p in places:
            if p.point and p.name and p.kind in readiness.place_sections:
                names_at[(round(p.point[0], 6), round(p.point[1], 6))].add(p.name.casefold())
        crowded = {
            k for k, names in names_at.items() if len(names) >= blocks.shared_coordinate_names
        }
        shared |= {
            (p.page_id, p.ordinal)
            for p in places
            if p.point and (round(p.point[0], 6), round(p.point[1], 6)) in crowded
        }

    listing_qids = {p.qid for ps in places_by_city.values() for p in ps if p.qid}
    listing_qids |= {f["wikidata_id"] for f in facts.values() if f.get("wikidata_id")}
    listing_qids |= {(r.get("wikidata") or "").strip() for r in listings} - {""}
    destination_qids = {d["wikidata_id"] for d in destinations.values() if d.get("wikidata_id")}
    centres: dict[int, tuple[float, float]] = {}
    for pid in cities:
        d = destinations.get(pid)
        if d is None or blocks.blocked_flags & set(d.get("flags") or []):
            continue
        centre = _point(d.get("coord_lat"), d.get("coord_long"))
        if centre:
            centres[pid] = centre

    centre_index = CentreIndex(centres)
    cache = ApiCache(cache_dir, today, rules.cache_max_age_days, sleep=sleep)
    search = _Searcher(cache, client, sleep)
    read_at = today.isoformat()

    # --- step 1: extra places from Wikidata -----------------------------------------------------
    places_skipped: Counter = Counter()
    searched_cities = sorted(pid for pid in short if pid in centres)
    places_skipped["city_without_centre"] = len(short) - len(searched_cities)
    near: dict[int, list[tuple[str, float]]] = {}
    for pid, hits in _run_bounded(
        searched_cities,
        lambda pid: search(
            WIKIDATA_API, centres[pid], int(rules.places_radius_km * 1000), rules.places_limit, {}
        ),
        "Wikidata near cities",
        workers=SEARCH_WORKERS,
    ):
        found: list[tuple[str, float]] = []
        if hits is None:
            places_skipped["city_search_busy"] += 1
            hits = []
        for h in hits:
            qid = str(h.get("title") or "")
            if not (qid.startswith("Q") and qid[1:].isdigit()):
                continue
            if qid in destination_qids:
                places_skipped["destination"] += 1
            elif qid in listing_qids:
                places_skipped["same_as_listing"] += 1
            else:
                found.append((qid, float(h.get("dist") or 0.0)))
        near[pid] = found
    city_qids = {destinations[pid]["wikidata_id"] for pid in searched_cities}
    items = _fetch_entities(
        cache, client, {q for found in near.values() for q, _ in found} | (city_qids - {None})
    )

    provisional: dict[int, list[tuple[str, ItemFacts, str, float]]] = {}
    for pid in searched_cities:
        city_item = items.get(destinations[pid].get("wikidata_id") or "")
        allowed = set(city_item.countries) if city_item else set()

        def rank(entry: tuple[str, float]) -> tuple[Any, ...]:
            item = items.get(entry[0])
            return (
                item is None or item.enwiki_title is None,
                item is None or not item.heritage,
                entry[1],
                int(entry[0][1:]),
            )

        known = [
            KnownPlace(p.point, place_words(p.name, selection), p.qid)
            for p in places_by_city.get(pid, [])
            if p.point
        ]
        kept: list[tuple[str, ItemFacts, str, float]] = []
        for qid, _dist in sorted(near.get(pid, []), key=rank):
            item = items.get(qid)
            if item is None:
                places_skipped["item_missing"] += 1
                continue
            if not item.label:
                places_skipped["no_label"] += 1
                continue
            if item.coordinate_status != "ok" or item.coordinate is None:
                places_skipped["no_point"] += 1
                continue
            if not item.image:
                places_skipped["no_image"] += 1
                continue
            place_kind, _ = kind_of(item, kind_map)
            if place_kind not in rules.kinds:
                places_skipped["kind"] += 1
                continue
            if not allowed:
                places_skipped["country_unknown"] += 1
                continue
            if not set(item.countries) & allowed:
                places_skipped["other_country"] += 1
                continue
            point = (item.coordinate.lat, item.coordinate.lon)
            if centre_index.nearest(point, rules.places_radius_km, pid) != pid:
                places_skipped["nearer_other_city"] += 1
                continue
            words = place_words(item.label, selection)
            if any(same_place(point, words, qid, k, rules) for k in known):
                places_skipped["same_as_listing"] += 1
                continue
            known.append(KnownPlace(point, words, qid))
            kept.append((qid, item, place_kind, distance_km(point, centres[pid])))
        provisional[pid] = kept

    p18_titles = {f"File:{item.image}" for kept in provisional.values() for _, item, _, _ in kept}

    # --- step 2: Commons files near places without a photo --------------------------------------
    subjects: list[Place] = []
    for pid in sorted(short):
        for p in places_by_city.get(pid, []):
            key = (p.page_id, p.ordinal)
            if key not in owners or key in photographed:
                continue
            if not rules.geosearch_enabled:
                subjects_skipped["geosearch_off"] += 1
            elif p.point is None:
                subjects_skipped[point_status[key]] += 1
            elif key in shared:
                subjects_skipped["coord_shared"] += 1
            else:
                subjects.append(p)
    geo_hits: dict[Place, list[GeoHit]] = {}
    for subject, hits in _run_bounded(
        subjects,
        lambda s: search(
            COMMONS_API,
            s.point,
            rules.search_radius_m,
            rules.geosearch_limit,
            {"gsnamespace": 6, "gsprimary": "all"},
        ),
        "Commons files near places",
        workers=SEARCH_WORKERS,
    ):
        found_hits: dict[str, GeoHit] = {}
        if hits is None:
            subjects_skipped["search_busy"] += 1
            hits = []
        for h in hits:
            point = _hit_point(h)
            title = str(h.get("title") or "")
            if point is None or not title.startswith("File:"):
                continue
            dist = float(h.get("dist") or 0.0)
            if title not in found_hits or dist < found_hits[title].distance_m:
                found_hits[title] = GeoHit(title, point, dist)  # a file with two points: nearest
        geo_hits[subject] = sorted(found_hits.values(), key=lambda g: (g.distance_m, g.title))

    geosearch_skipped: Counter = Counter()
    accepted_by_subject: dict[Place, list[Any]] = {}
    for subject in subjects:
        # The city's own name says nothing about which place a file shows: "Narmada in
        # Omkareshwar" is not "Omkareshwar Shiva Temple". Those words never count as a match.
        city_words = place_words(str(cities[subject.owner].get("title") or ""), selection)
        others = [
            OtherPlace(p.point, place_words(p.name, selection) - city_words)
            for p in places_by_city[subject.owner]
            if p.point and (p.page_id, p.ordinal) != (subject.page_id, subject.ordinal)
        ] + [
            OtherPlace(
                (item.coordinate.lat, item.coordinate.lon),
                place_words(item.label or "", selection) - city_words,
            )
            for _, item, _, _ in provisional.get(subject.owner, [])
            if item.coordinate
        ]
        accepted = accept_hits(
            geo_hits.get(subject, []),
            place_words(subject.name, selection) - city_words,
            others,
            rules,
            selection,
            hero,
            geosearch_skipped,
        )
        accepted_by_subject[subject] = accepted[: rules.max_photos_per_place * 3]
    geo_titles = {a.hit.title for acc in accepted_by_subject.values() for a in acc}
    infos: dict[str, FileInfo] = fetch_file_info(client, p18_titles | geo_titles, photo_rules)

    photos_failed: Counter = Counter()
    dump_iso = dump_date

    def checked_row(
        subject: Subject, title: str, source: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        info = infos[title]
        candidate = Candidate({source})
        row = candidate_row(subject, title, candidate, info, photo_rules, dump_iso, read_at)
        problem = photo_problem(info, _photo_shape(row), photo_rules, selection, hero)
        return (None, problem) if problem else (row, None)

    place_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for pid in searched_cities:
        city = {**cities[pid]}
        taken = 0
        for qid, item, place_kind, centre_km in provisional[pid]:
            if taken >= rules.max_per_city:
                places_skipped["over_city_cap"] += 1
                continue
            title = f"File:{item.image}"
            extra = _extra_row(city, qid, item, place_kind, centre_km, rules, dump_date, read_at)
            if infos[title].title.removeprefix("File:") in used_files:
                places_skipped["photo_already_used"] += 1
                continue
            subject = Subject(
                pid,
                extra["ordinal"],
                "listing",
                extra["listing_key"],
                city.get("tree_country_iso"),
                qid,
                item.image,
            )
            row, problem = checked_row(subject, title, "wikidata_p18")
            if row is None:
                places_skipped[f"photo_{problem}"] += 1
                continue
            used_files.add(row["commons_file"])
            taken += 1
            place_rows.append(extra)
            candidate_rows.append(
                {
                    **row,
                    "subject_name": item.label,
                    "nearby_source": "wikidata_item",
                    "nearby_distance_m": None,
                    "nearby_named": None,
                    "nearby_rules_version": rules.version,
                }
            )

    with_candidate = 0
    for subject in subjects:
        kept_rows = 0
        for accepted in accepted_by_subject[subject]:
            if kept_rows >= rules.max_photos_per_place:
                break
            info = infos[accepted.hit.title]
            if info.title.removeprefix("File:") in used_files:
                geosearch_skipped["photo_of_another_place"] += 1
                continue
            row, problem = checked_row(
                Subject(
                    subject.page_id,
                    subject.ordinal,
                    "listing",
                    subject.listing_key,
                    subject.country_iso,
                    subject.qid,
                    None,
                ),
                accepted.hit.title,
                "commons_geosearch",
            )
            if row is None:
                photos_failed[problem] += 1
                continue
            kept_rows += 1
            candidate_rows.append(
                {
                    **row,
                    "subject_name": subject.name,
                    "nearby_source": "commons_geosearch",
                    "nearby_distance_m": round(accepted.hit.distance_m, 1),
                    "nearby_named": accepted.named,
                    "nearby_rules_version": rules.version,
                }
            )
        with_candidate += kept_rows > 0

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    place_rows.sort(key=lambda r: (r["page_id"], r["ordinal"]))
    candidate_rows.sort(key=lambda r: (r["page_id"], r["ordinal"], r["commons_file"]))
    with JsonlWriter(target / PLACES_FILE) as out:
        for r in place_rows:
            out.write(r)
    with JsonlWriter(target / CANDIDATES_FILE) as out:
        for r in candidate_rows:
            out.write(r)
    totals = {
        "cities": len(cities),
        "min_photographed_places": readiness.min_photographed_places,
        "short_cities": len(short),
        "extra_places": len(place_rows),
        "cities_with_extra_places": len({r["owner_page_id"] for r in place_rows}),
        "geosearch_enabled": rules.geosearch_enabled,
        "subjects": len(subjects),
        "subjects_with_candidate": with_candidate,
        "candidate_rows": len(candidate_rows),
        "searches_busy": search.busy,
        "places_skipped": {k: v for k, v in sorted(places_skipped.items()) if v},
        "subjects_skipped": dict(sorted(subjects_skipped.items())),
        "geosearch_skipped": dict(sorted(geosearch_skipped.items())),
        "photos_failed": dict(sorted(photos_failed.items())),
        "cache": {"hits": cache.hits, "misses": cache.misses},
    }
    write_text(target / "report.md", _report(totals, dump_date, rules))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "nearby_rules_version": rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "photo_candidates_manifest_sha256": candidates_manifest,
            "inputs_sha256": dict(sorted(inputs.items())),
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return NearbyResult(target, totals)
