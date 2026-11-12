"""`enrich-wikidata` (B4a): Wikidata facts for every article and for listings that have an item.

What it does, in order (design: catalogue-pipeline.md job 3, plus the owner's rulings of
2026-11-12):
1. Check that the discover and fetch-listings outputs belong together (hashes).
2. Find a Wikidata ID for every article (discover's, else Wikivoyage `pageprops`), and for
   listings that only have a `wikipedia=` link, resolve the link through English Wikipedia.
3. Fetch the items (50 per request), then the items they refer to (classes, designations, time
   zones, fee units) for their labels and IANA names.
4. Decide coordinates (`coords.py`), UNESCO, kind and time zone, and count fee statements.
5. Write rows, review tables, a report and a manifest to `out/enrich/<date>[-CC]/`.

No value is guessed: where a rule cannot decide, the field stays empty and a flag says why.
Nothing here is country-specific. Fee amounts are counted and sampled for review only.
"""

import hashlib
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover.confirm import CountryInfo, _acceptable_countries, fetch_wikidata_ids
from catalogue.discover.output import write_manifest
from catalogue.enrich import report
from catalogue.enrich.claims import ItemFacts
from catalogue.enrich.coords import CoordDecision, country_check, decide_coordinate
from catalogue.enrich.facts import (
    WORLD_HERITAGE_SITE,
    fee_summary,
    kind_of,
    timezone_of,
    unesco_status,
)
from catalogue.enrich.inputs import (
    ArticleIn,
    ListingIn,
    iter_listings,
    read_articles,
    read_destination_states,
    read_page_geo,
    verify_inputs,
)
from catalogue.enrich.items import ItemsResult, fetch_items
from catalogue.enrich.kindmap import load_kind_map
from catalogue.enrich.links import LinkResult, resolve_wikipedia_links
from catalogue.enrich.rules import EnrichRules, load_enrich_rules
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.listings.output import JsonlWriter, SortedJsonl
from catalogue.listings.quality import QualityRules, load_rules, parse_coordinate, valid_wikidata
from catalogue.listings.run import parse_dump_date

SCHEMA_VERSION = 1
LICENCE = "CC0 1.0"
LICENCE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ATTRIBUTION = "Wikidata contributors"
DESTINATION_ROLES = frozenset({"destination", "destination_outline"})
FEE_KINDS = frozenset({"see", "do"})
EXPECTED_LABELS = {WORLD_HERITAGE_SITE: "World Heritage Site"}
NO_COUNTRY = "(none)"


@dataclass(slots=True)
class CoordCount:
    missing_before: int = 0
    filled: int = 0
    filled_via_link: int = 0
    flags: Counter = field(default_factory=Counter)


@dataclass(slots=True)
class Tally:
    coords: dict[tuple[str, str], CoordCount] = field(default_factory=dict)
    link_status: Counter = field(default_factory=Counter)
    link_rows: list[tuple[int, int, str, str, str, str]] = field(default_factory=list)
    heritage: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    unmapped: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    fee_items: dict[tuple[str, str], ItemFacts] = field(default_factory=dict)
    timezone_basis: Counter = field(default_factory=Counter)
    unesco: Counter = field(default_factory=Counter)

    def count(self, country: str | None, subject: str) -> CoordCount:
        return self.coords.setdefault((country or NO_COUNTRY, subject), CoordCount())


@dataclass(frozen=True, slots=True)
class EnrichResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        return report.summary_text(self.totals)


def _dashed(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _provenance(
    qid: str | None, item: ItemFacts | None, *, dump_date: str, read_at: str
) -> dict[str, Any]:
    if qid and item and item.revision_id:
        url = f"https://www.wikidata.org/w/index.php?title={qid}&oldid={item.revision_id}"
    else:
        url = f"https://www.wikidata.org/wiki/{qid}" if qid else None
    return {
        "schema_version": SCHEMA_VERSION,
        "source_url": url,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "dump_date": _dashed(dump_date),
        "read_at": read_at,
    }


def _item_fields(
    item: ItemFacts | None, kind_map: Mapping[str, str], tally: Tally, key_country: str | None
) -> tuple[dict[str, Any], list[str]]:
    """The facts every row carries when it has an item, and the flags found while reading them."""
    flags: list[str] = []
    is_unesco, unesco_flags = unesco_status(item)
    kind, kind_flags = kind_of(item, kind_map)
    flags += [*unesco_flags, *kind_flags]
    if item is not None:
        tally.unesco["unesco" if is_unesco else "not_unesco"] += 1
        for designation in item.heritage:
            tally.heritage[(key_country or NO_COUNTRY, designation)].add(item.qid)
        if kind is None:
            for cls in item.instance_of:
                if cls not in kind_map:
                    tally.unmapped[cls].add(item.qid)
    fields = {
        "is_unesco": is_unesco,
        "heritage": list(item.heritage) if item else [],
        "instance_of": list(item.instance_of) if item else [],
        "kind": kind,
        "image": item.image if item else None,
        "description": item.description if item else None,
        "label": item.label if item else None,
        "enwiki_title": item.enwiki_title if item else None,
        "wikidata_revision_id": item.revision_id if item else None,
    }
    return fields, flags


def _coordinate_fields(own: tuple[float, float] | None, decision: CoordDecision | None) -> dict:
    filled = bool(decision and decision.filled)
    if own is not None:
        lat, lon, source = own[0], own[1], "wikivoyage"
    elif filled and decision:
        lat, lon, source = decision.lat, decision.lon, "wikidata"
    else:
        lat = lon = source = None
    return {
        "own_lat": own[0] if own else None,
        "own_long": own[1] if own else None,
        "coord_lat": lat,
        "coord_long": lon,
        "coord_source": source,
        "coord_filled": filled,
        "wikidata_lat": decision.wikidata_lat if decision else None,
        "wikidata_long": decision.wikidata_lon if decision else None,
        "disagreement_km": round(decision.disagreement_km, 3)
        if decision and decision.disagreement_km is not None
        else None,
    }


def _own_point(lat: str, long: str) -> tuple[tuple[float, float] | None, bool]:
    """(usable point, whether there is a value that is not usable)."""
    point = parse_coordinate(lat, long)
    return point, point is None and bool(lat.strip() or long.strip())


def _check_world_heritage_label(items: Mapping[str, ItemFacts]) -> None:
    for qid, label in EXPECTED_LABELS.items():
        found = items.get(qid)
        if found is None or found.label != label:
            raise PipelineError(
                f"{qid} is not labelled {label!r} on Wikidata; the UNESCO rule needs checking"
            )


def _references(items: Mapping[str, ItemFacts]) -> set[str]:
    wanted = set(EXPECTED_LABELS)
    for item in items.values():
        wanted.update(item.instance_of, item.heritage, item.time_zones)
        wanted.update(f.unit_qid for f in item.fees if f.unit_qid)
    return wanted - set(items)


def run_enrich_wikidata(
    client: PoliteClient,
    *,
    discover_folder: Path,
    listings_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    detail_countries: Sequence[str] = (),
    rules: EnrichRules | None = None,
    quality_rules: QualityRules | None = None,
    kind_map: Mapping[str, str] | None = None,
) -> EnrichResult:
    data_date = parse_dump_date(dump_date)
    rules = rules if rules is not None else load_enrich_rules()
    quality_rules = quality_rules if quality_rules is not None else load_rules()
    kind_map = kind_map if kind_map is not None else load_kind_map()
    country_filter = country_iso.upper() if country_iso else None

    verify_inputs(discover_folder, listings_folder, country_filter=country_filter)
    # fetch-listings filters by country but discover's files are always the whole world, so an
    # India run must not look up (or write facts for) any other country's articles.
    articles = [
        a
        for a in read_articles(discover_folder)
        if country_filter is None or a.tree_country_iso == country_filter
    ]
    states = read_destination_states(discover_folder)
    page_geo = read_page_geo(listings_folder)
    listings = list(iter_listings(listings_folder))

    known_countries = {a.tree_country_iso for a in articles if a.tree_country_iso}
    for iso in detail_countries:
        if iso.upper() not in known_countries:
            raise PipelineError(f"unknown country code {iso.upper()} for --detail; check the code")

    # --- Wikidata IDs ------------------------------------------------------------------------
    article_qid: dict[int, tuple[str, str]] = {}  # page id -> (qid, source)
    looked_up = fetch_wikidata_ids(
        client, [a.title for a in articles if not valid_wikidata(a.wikidata_id or "")]
    )
    for a in articles:
        known = valid_wikidata(a.wikidata_id or "")
        found = valid_wikidata(looked_up.get(a.title, ""))
        if known:
            article_qid[a.page_id] = (known, "discover")
        elif found:
            article_qid[a.page_id] = (found, "pageprops")

    link_values = sorted(
        {x.wikipedia for x in listings if not valid_wikidata(x.wikidata) and x.wikipedia.strip()}
    )
    links = resolve_wikipedia_links(client, link_values)

    def listing_qid(x: ListingIn) -> tuple[str | None, str | None, LinkResult | None]:
        known = valid_wikidata(x.wikidata)
        if known:
            return known, "wikivoyage", None
        if x.wikipedia.strip():
            link = links[x.wikipedia]
            return (link.qid, "wikipedia_link", link) if link.resolved else (None, None, link)
        return None, None, None

    listing_ids = [listing_qid(x) for x in listings]

    # --- the items ---------------------------------------------------------------------------
    wanted = {q for q, _ in article_qid.values()} | {q for q, _, _ in listing_ids if q}
    first = fetch_items(client, wanted)
    second = fetch_items(client, _references(first.items))
    items = {**second.items, **first.items}
    _check_world_heritage_label(items)

    def resolve(qid: str, result: ItemsResult = first) -> tuple[ItemFacts | None, str | None]:
        if qid in result.redirects:
            return None, "item_merged"
        if qid in result.items:
            return result.items[qid], None
        return None, "item_missing"

    country_qid = {
        a.tree_country_iso: article_qid[a.page_id][0]
        for a in articles
        if a.type == "country" and a.tree_country_iso and a.page_id in article_qid
    }
    acceptable: dict[str, frozenset[str]] = {}
    for iso, qid in country_qid.items():
        country_item = first.items.get(qid)
        parents = frozenset(country_item.countries) - {qid} if country_item else frozenset()
        acceptable[iso] = _acceptable_countries(CountryInfo(qid, iso, parents))
    destination_qids = {q for q, _ in article_qid.values()}

    tally = Tally()
    read_at = today.isoformat()

    # --- articles ----------------------------------------------------------------------------
    destination_rows: list[dict[str, Any]] = []
    for a in articles:
        destination_rows.append(
            _article_row(
                a,
                article_qid,
                resolve,
                page_geo,
                states,
                acceptable,
                country_qid,
                items,
                kind_map,
                rules,
                tally,
                dump_date,
                read_at,
            )
        )

    # --- listings ----------------------------------------------------------------------------
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    with SortedJsonl(target / "listing_facts.jsonl") as listing_out:
        for x, (qid, source, link) in zip(listings, listing_ids, strict=True):
            if link is not None:
                tally.link_status[link.status] += 1
                tally.link_rows.append(
                    (x.page_id, x.ordinal, x.listing_key, x.wikipedia, link.status, link.qid or "")
                )
            own, own_invalid = _own_point(x.lat, x.long)
            counts = tally.count(x.country_iso, "listings")
            if own is None:
                counts.missing_before += 1
            if qid is None and own is not None:
                continue  # nothing to say about this listing
            if qid is None:
                bad = bool(x.wikidata.strip())
                item, problem = None, "bad_wikidata_id" if bad else "no_wikidata_id"
            else:
                item, problem = resolve(qid)
            decision = decide_coordinate(
                own_point=own,
                own_invalid=own_invalid,
                item=item,
                item_problem=problem,
                country=country_check(item, acceptable.get(x.country_iso or "", frozenset())),
                reference_point=parse_coordinate(x.page_lat, x.page_long),
                limit_km=quality_rules.far_km.get(x.page_type or ""),
                needs_reference=True,
                is_destination_item=qid in destination_qids,
                disagreement_km=rules.disagreement_km,
            )
            if decision.filled:
                counts.filled += 1
                counts.filled_via_link += source == "wikipedia_link"
            fields, extra = _item_fields(item, kind_map, tally, x.country_iso)
            flags = sorted({*decision.flags, *extra})
            counts.flags.update(flags)
            if item is not None and x.kind in FEE_KINDS:
                tally.fee_items[(x.country_iso or NO_COUNTRY, item.qid)] = item
            listing_out.add(
                {
                    "page_id": x.page_id,
                    "ordinal": x.ordinal,
                    "listing_key": x.listing_key,
                    "listing_kind": x.kind,
                    "country_iso": x.country_iso,
                    "wikidata_id": qid,
                    "wikidata_source": source,
                    "wikipedia_status": link.status if link else None,
                    **_coordinate_fields(own, decision),
                    **fields,
                    "flags": flags,
                    **_provenance(qid, item, dump_date=dump_date, read_at=read_at),
                }
            )
        listing_out.finish()

    with JsonlWriter(target / "destination_facts.jsonl") as destination_out:
        for row in destination_rows:
            destination_out.write(row)

    fees = {key: fee_summary(item, items, data_date) for key, item in tally.fee_items.items()}

    names = {a.tree_country_iso: a.tree_country for a in articles if a.tree_country_iso}
    totals = report.write_all(
        target,
        tally=tally,
        fees=fees,
        items=items,
        articles=articles,
        listing_count=len(listings),
        listing_rows=listing_out.count,
        first=first,
        second=second,
        country_names={iso: name for iso, name in names.items() if name},
        detail_countries=[c.upper() for c in detail_countries],
        dump_date=dump_date,
    )
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "enrich_rules_version": rules.version,
            "quality_rules_version": quality_rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "listings_manifest_sha256": hashlib.sha256(
                (listings_folder / "manifest.json").read_bytes()
            ).hexdigest(),
            "totals": totals,
            "outputs_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(target.iterdir())
                if path.is_file() and path.name != "manifest.json"
            },
        },
    )
    return EnrichResult(target, totals)


def _article_row(
    a: ArticleIn,
    article_qid: Mapping[int, tuple[str, str]],
    resolve: Any,
    page_geo: Mapping[int, tuple[str, str]],
    states: Mapping[int, tuple[str, str | None]],
    acceptable: Mapping[str, frozenset[str]],
    country_qid: Mapping[str, str],
    items: Mapping[str, ItemFacts],
    kind_map: Mapping[str, str],
    rules: EnrichRules,
    tally: Tally,
    dump_date: str,
    read_at: str,
) -> dict[str, Any]:
    qid, source = article_qid.get(a.page_id, (None, None))
    item, problem = resolve(qid) if qid else (None, "no_wikidata_id")
    own, own_invalid = _own_point(*page_geo.get(a.page_id, ("", "")))
    is_destination = a.role in DESTINATION_ROLES
    decision: CoordDecision | None = None
    flags: list[str] = []
    if is_destination:
        counts = tally.count(a.tree_country_iso, "destinations")
        if own is None:
            counts.missing_before += 1
        country = country_check(item, acceptable.get(a.tree_country_iso or "", frozenset()))
        state = states.get(a.page_id)
        if state and state[0] != "confirmed":
            country = "disagrees" if state[0] == "conflict" else "unknown"
        decision = decide_coordinate(
            own_point=own,
            own_invalid=own_invalid,
            item=item,
            item_problem=problem,
            country=country,
            reference_point=None,
            limit_km=None,
            needs_reference=False,
            is_destination_item=False,
            disagreement_km=rules.disagreement_km,
        )
        flags += decision.flags
        counts.filled += decision.filled
        counts.flags.update(decision.flags)
    elif qid and problem:
        flags.append(problem)
    country_item = items.get(country_qid.get(a.tree_country_iso or "", ""))
    zone = None
    if is_destination:
        zone = timezone_of(item, items, country_item)
        flags += zone.flags
        tally.timezone_basis[zone.basis or "none"] += 1
    fields, extra = _item_fields(item, kind_map, tally, a.tree_country_iso)
    flags += extra
    coordinate = _coordinate_fields(own, decision)
    if decision is None and item is not None and item.coordinate is not None:
        coordinate["wikidata_lat"], coordinate["wikidata_long"] = (
            item.coordinate.lat,
            item.coordinate.lon,
        )
    return {
        "wikivoyage_page_id": a.page_id,
        "title": a.title,
        "role": a.role,
        "tree_country_iso": a.tree_country_iso,
        "wikidata_id": qid,
        "wikidata_source": source,
        **coordinate,
        **fields,
        "timezone": zone.name if zone else None,
        "timezone_basis": zone.basis if zone else None,
        "flags": sorted(set(flags)),
        **_provenance(qid, item, dump_date=dump_date, read_at=read_at),
    }
