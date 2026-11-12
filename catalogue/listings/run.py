"""The `fetch-listings` job: dump + discover article list -> listings, sections, markers.

Offline: no network, no database, no settings. It refuses to run unless the dump is the very file
`discover` read (its SHA-1 must equal the one in the discover manifest). Pages are read in dump
order and written as they are parsed, so memory stays small and two runs write identical bytes.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from catalogue.discover.dump import sha1_of
from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.discover.parse import DumpPage, iter_pages
from catalogue.discover.slugs import RegistryEntry, load_registry
from catalogue.errors import PipelineError
from catalogue.listings import report
from catalogue.listings.interpret import Interpreted, interpret
from catalogue.listings.keys import KEY_VERSION, KeyInput, address_label, assign_keys, key_slug
from catalogue.listings.output import SCHEMA_VERSION, SortedJsonl, provenance
from catalogue.listings.quality import (
    DupInput,
    QualityRules,
    coordinate_flags,
    distance_km,
    find_duplicates,
    gap_group,
    load_rules,
    normalised_name,
    parse_coordinate,
    shape_flags,
    valid_wikidata,
)
from catalogue.listings.reference import Reference, load_reference
from catalogue.listings.rollup import Owner, resolve_owner
from catalogue.listings.source import read_articles
from catalogue.listings.wikitext import RawListing, marker_listing, parse_page
from catalogue.progress import Progress

# Typed markers on these page types are real places on the rendered page, so they become listings.
MARKER_LISTING_PAGE_TYPES = frozenset({"city", "district", "park"})
DUPLICATE_SAMPLES = 100  # rows per basis written to duplicates.csv for review
PRICE_SAMPLES = 100  # rows per reason written to prices_unparsed.csv for review


@dataclass(frozen=True, slots=True)
class ListingsResult:
    output_dir: Path
    totals: dict[str, Any]
    owner_counts: dict[int, dict[str, int]]

    def summary(self) -> str:
        totals = self.totals
        kinds = ", ".join(f"{k} {totals['by_kind'].get(k, 0)}" for k in report.KIND_ORDER)
        return "\n".join(
            [
                f"pages read: {totals['pages_read']}",
                f"pages that failed to parse: {totals['pages_failed']}",
                f"listings: {totals['listings']} ({kinds})",
                f"  rolled up from districts: {totals['via_district']}, "
                f"no owner: {totals['owner_unresolved']}",
                f"section texts: {totals['sections']}",
                f"map markers: {totals['markers']}",
            ]
        )


def parse_dump_date(dump_date: str) -> date:
    """`20261101` -> a date; anything else is one clear error, not a failure on every page."""
    try:
        return datetime.strptime(dump_date, "%Y%m%d").date()  # noqa: DTZ007 (a calendar day)
    except ValueError:
        raise PipelineError(f"the dump date must be YYYYMMDD, not {dump_date!r}") from None


def _verify_dump(dump_path: Path, discover_folder: Path) -> str:
    manifest_path = discover_folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"manifest.json not found in {discover_folder}; run discover first")
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))["dump_sha1"]
    except (ValueError, KeyError, TypeError):
        raise PipelineError("the discover manifest.json is damaged; run discover again") from None
    if not dump_path.is_file():
        raise PipelineError(f"the dump {dump_path.name} was not found; run discover to get it")
    actual = sha1_of(dump_path)
    if actual != expected:
        raise PipelineError(
            f"the dump {dump_path.name} does not match the one discover used (checksum differs)"
        )
    return actual


class _Tally:
    """Counts kept while streaming; only numbers, never listing text."""

    def __init__(self) -> None:
        self.stats: dict[str, report.CountryStats] = {}
        self.owner_counts: dict[int, dict[str, int]] = {}
        self.key_inputs: list[KeyInput] = []
        self.failures: list[tuple[int, str, str]] = []
        self.by_kind: Counter[str] = Counter()
        self.by_role: Counter[str] = Counter()
        self.dup_inputs: list[DupInput] = []
        self.flags: Counter[str] = Counter()
        self.dup_basis: Counter[str] = Counter()
        self.gap: Counter[str] = Counter()
        self.gap_rows: dict[tuple[str, str, str], Counter[str]] = {}
        self.dup_samples: list[list[Any]] = []
        self.price_samples: dict[str, list[tuple[int, int, str]]] = {}  # page, ordinal, text
        self.price_status: Counter[str] = Counter()
        self.hours_status: Counter[str] = Counter()
        self.lastedit_status: Counter[str] = Counter()
        self.price_age: Counter[str] = Counter()
        self.hours_age: Counter[str] = Counter()
        self.pages_read = 0
        self.marker_listings = 0
        self.pages_not_in_articles = 0
        self.via_district = 0
        self.owner_unresolved = 0

    def country(self, name: str | None, iso: str | None) -> report.CountryStats:
        label = name or report.NO_COUNTRY
        return self.stats.setdefault(label, report.CountryStats(label, iso or ""))


@dataclass(slots=True)
class _PageRows:
    """Everything one page contributes, built before any of it is written."""

    sections: list[dict[str, Any]]
    listings: list[dict[str, Any]]
    markers: list[dict[str, Any]]
    key_inputs: list[KeyInput]
    counted: list[tuple[RawListing, str, Interpreted]]  # listing, its section, what was read
    owner: Owner | None
    region: str
    marker_listings: int = 0
    dup_inputs: list[DupInput] = field(default_factory=list)


def _quality_fields(
    fields: dict[str, str],
    point: tuple[float, float] | None,
    distance: float | None,
    page_type: str | None,
    rules: QualityRules,
) -> dict[str, Any]:
    """The flags and the Wikidata-gap group of one listing. Duplicate fields are filled in when
    the files are finished, because they need every listing of the owner."""
    flags = [
        *coordinate_flags(
            fields["lat"], fields["long"], distance_km=distance, page_type=page_type, rules=rules
        ),
        *shape_flags(fields),
    ]
    return {
        "quality_flags": flags,
        "coord_gap": gap_group(
            has_coordinates=point is not None,
            wikidata=fields["wikidata"],
            wikipedia=fields["wikipedia"],
        ),
        "duplicate_of": None,
        "duplicate_basis": None,
        "same_place_as": None,
    }


def _build_page(
    dump_page: DumpPage,
    row: dict[str, Any],
    by_title: dict[str, dict[str, Any]],
    slugs: dict[int, RegistryEntry],
    dump_date: str,
    dump_day: date,
    read_at: str,
    reference: Reference,
    rules: QualityRules,
) -> _PageRows:
    content = parse_page(dump_page.text)
    owner = resolve_owner(row, by_title)
    slug = slugs[owner.page_id].slug if owner and owner.page_id in slugs else ""
    page_fields = {
        "page_id": dump_page.page_id,
        "page_title": dump_page.title,
        "page_type": row["type"],
        "role": row["role"],
        "country": row["tree_country"],
        "country_iso": row["tree_country_iso"],
        "page_lat": content.geo[0] if content.geo else "",
        "page_long": content.geo[1] if content.geo else "",
    }
    page_point = parse_coordinate(*content.geo) if content.geo else None
    prov = provenance(dump_page, dump_date=dump_date, read_at=read_at)
    ancestors = row["ancestors"]
    built = _PageRows(
        [],
        [],
        [],
        [],
        [],
        owner,
        ancestors[-2] if len(ancestors) >= 2 else "(directly under the country)",
    )
    ordinal = 0
    marker_number = 0
    marker_listings_allowed = row["type"] in MARKER_LISTING_PAGE_TYPES
    for number, section in enumerate(content.sections, start=1):
        items = list(section.listings)
        for marker in section.markers:
            promoted = marker_listing(marker) if marker_listings_allowed else None
            if promoted is not None:
                items.append(promoted)
                built.marker_listings += 1
            else:
                marker_number += 1
                built.markers.append({**page_fields, **prov, **marker, "ordinal": marker_number})
        built.sections.append(
            {
                **page_fields,
                **prov,
                "section_heading": section.heading,
                "section_key": section.key,
                "standard": section.standard,
                "kind": section.kind,
                "text": section.text,
                "listing_count": len(items),
                "ordinal": number,
            }
        )
        for listing in items:
            ordinal += 1
            fields = listing.fields
            read = interpret(
                fields, country_iso=row["tree_country_iso"], reference=reference, dump_day=dump_day
            )
            point = parse_coordinate(fields["lat"], fields["long"])
            distance = round(distance_km(page_point, point), 3) if page_point and point else None
            base = f"{listing.kind}:{key_slug(fields['name'])}"
            built.key_inputs.append(
                KeyInput(
                    scope=owner.page_id if owner else dump_page.page_id,
                    base=base,
                    page_id=dump_page.page_id,
                    ordinal=ordinal,
                    wikidata=fields["wikidata"],
                    lat=fields["lat"],
                    long=fields["long"],
                    address=fields["address"],
                )
            )
            built.dup_inputs.append(
                DupInput(
                    scope=owner.page_id if owner else dump_page.page_id,
                    kind=listing.kind,
                    name=normalised_name(fields["name"]),
                    wikidata=valid_wikidata(fields["wikidata"]),
                    point=point,
                    address=address_label(fields["address"]),
                    page_id=dump_page.page_id,
                    ordinal=ordinal,
                    from_marker=listing.from_marker,
                )
            )
            built.listings.append(
                {
                    **page_fields,
                    **prov,
                    **fields,
                    **read.fields,
                    **_quality_fields(fields, point, distance, row["type"], rules),
                    "coord_distance_km": distance,
                    "kind": listing.kind,
                    "listing_type": listing.listing_type,
                    "extra": listing.extra,
                    "repeated_params": list(listing.repeated_params),
                    "section_key": section.key,
                    "ordinal": ordinal,
                    "listing_key": base,  # made unique when the files are finished
                    "key_version": KEY_VERSION,
                    "from_marker": listing.from_marker,
                    "owner_page_id": owner.page_id if owner else None,
                    "owner_title": owner.title if owner else "",
                    "owner_type": owner.type if owner else None,
                    "owner_status": owner.status if owner else None,
                    "owner_slug": slug,
                    "via_district": owner.via_district if owner else False,
                }
            )
            built.counted.append((listing, section.key, read))
    return built


def run_fetch_listings(
    *,
    dump_path: Path,
    dump_date: str,
    discover_folder: Path,
    out_dir: Path,
    registry_path: Path,
    today: date,
    country_iso: str | None = None,
    detail_countries: Sequence[str] = (),
    proving_titles: Sequence[str] = (),
    reference: Reference | None = None,
    rules: QualityRules | None = None,
) -> ListingsResult:
    dump_day = parse_dump_date(dump_date)
    reference = reference if reference is not None else load_reference()
    rules = rules if rules is not None else load_rules()
    country_filter = country_iso.upper() if country_iso else None
    dump_sha1 = _verify_dump(dump_path, discover_folder)
    articles_path = discover_folder / "articles.jsonl"
    by_id, by_title = read_articles(articles_path)
    articles_sha256 = hashlib.sha256(articles_path.read_bytes()).hexdigest()
    slugs = load_registry(registry_path)
    country_names = {
        r["tree_country_iso"]: r["tree_country"]
        for r in by_id.values()
        if r["tree_country_iso"] and r["tree_country"]
    }
    for iso in detail_countries:
        if iso.upper() not in country_names:
            raise PipelineError(f"unknown country code {iso.upper()} for --detail; check the code")

    folder = f"{dump_date}-{country_filter}" if country_filter else dump_date
    target = out_dir / folder
    read_at = today.isoformat()
    tally = _Tally()

    with (
        SortedJsonl(target / "listings.jsonl") as listings_out,
        SortedJsonl(target / "sections.jsonl") as sections_out,
        SortedJsonl(target / "markers.jsonl") as markers_out,
    ):
        progress = Progress("pages", len(by_id))
        for dump_page in iter_pages(dump_path):
            progress.advance()
            row = by_id.get(dump_page.page_id)
            if row is None:
                tally.pages_not_in_articles += 1
                continue
            if country_filter and row["tree_country_iso"] != country_filter:
                continue
            try:
                built = _build_page(
                    dump_page, row, by_title, slugs, dump_date, dump_day, read_at, reference, rules
                )
            except Exception as exc:  # noqa: BLE001
                # One unreadable page must not stop a world run. It is counted, listed by ID,
                # title and error class (never the message, which could quote page text) in
                # parse_failures.csv, and shown in the summary. Nothing of the page is written.
                tally.failures.append((dump_page.page_id, dump_page.title, type(exc).__name__))
                continue
            tally.pages_read += 1
            tally.marker_listings += built.marker_listings
            stats = tally.country(row["tree_country"], row["tree_country_iso"])
            for section_row in built.sections:
                sections_out.add(section_row)
            for listing_row in built.listings:
                listings_out.add(listing_row)
            for marker_row in built.markers:
                markers_out.add(marker_row)
            tally.key_inputs.extend(built.key_inputs)
            tally.dup_inputs.extend(built.dup_inputs)
            for listing, section_key, read in built.counted:
                _count(
                    tally,
                    stats,
                    listing,
                    section_key,
                    built.region,
                    built.owner,
                    row["role"],
                    today,
                    read,
                )
            for listing_row in built.listings:
                _sample_price(tally, listing_row)
                _note_quality(tally, stats, listing_row)
                if listing_row["coord_distance_km"] is not None:
                    page_type = listing_row["page_type"] or report.NO_TYPE
                    stats.distances.setdefault(page_type, []).append(
                        listing_row["coord_distance_km"]
                    )

        progress.done()
        keys = assign_keys(tally.key_inputs)
        listings_out.finish(_finisher(tally, keys, find_duplicates(tally.dup_inputs)))
        sections_out.finish()
        markers_out.finish()

    totals = {
        "pages_read": tally.pages_read,
        "pages_not_in_articles": tally.pages_not_in_articles,
        "pages_failed": len(tally.failures),
        "listings": listings_out.count,
        "sections": sections_out.count,
        "markers": markers_out.count,
        "marker_listings": tally.marker_listings,
        "via_district": tally.via_district,
        "owner_unresolved": tally.owner_unresolved,
        "by_kind": dict(tally.by_kind),
        "by_role": dict(tally.by_role),
        "price_status": dict(tally.price_status),
        "hours_status": dict(tally.hours_status),
        "lastedit_status": dict(tally.lastedit_status),
        "price_age": dict(tally.price_age),
        "hours_age": dict(tally.hours_age),
        "quality_flags": dict(sorted(tally.flags.items())),
        "duplicates_by_basis": dict(sorted(tally.dup_basis.items())),
        "wikidata_gap": dict(sorted(tally.gap.items())),
    }
    _write_price_samples(target, tally, keys)
    _write_reports(
        target,
        totals,
        tally,
        by_id,
        by_title,
        country_filter,
        detail_countries,
        country_names,
        proving_titles,
        dump_date,
    )
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "key_version": KEY_VERSION,
            "quality_rules_version": rules.version,
            "dump_date": dump_date,
            "dump_sha1": dump_sha1,
            "articles_sha256": articles_sha256,
            "country_filter": country_filter,
            "generated_on": read_at,
            "totals": totals,
            "outputs_sha256": {
                "listings.jsonl": listings_out.sha256,
                "sections.jsonl": sections_out.sha256,
                "markers.jsonl": markers_out.sha256,
            },
        },
    )
    return ListingsResult(output_dir=target, totals=totals, owner_counts=tally.owner_counts)


def _count(tally, stats, listing, section_key, region, owner, role, today, read) -> None:
    tally.by_kind[listing.kind] += 1
    tally.by_role[role] += 1
    stats.by_kind[listing.kind] += 1
    stats.by_section[section_key] += 1
    stats.by_region[region] += 1
    tally.lastedit_status[read.lastedit_status] += 1
    stats.lastedit_status[read.lastedit_status] += 1
    tally.price_status[read.price_status] += 1
    stats.price_status[read.price_status] += 1
    tally.hours_status[read.hours_status] += 1
    stats.hours_status[read.hours_status] += 1
    if read.price_status != "empty":
        tally.price_age[read.age] += 1
        stats.price_age[read.age] += 1
    if read.hours_status != "empty":
        tally.hours_age[read.age] += 1
        stats.hours_age[read.age] += 1
    bucket = report.lastedit_bucket(listing.fields["lastedit"], latest_year=today.year)
    stats.lastedit_years[bucket] += 1
    if owner is None:
        tally.owner_unresolved += 1
        stats.owner_unresolved += 1
        return
    stats.by_owner_status[owner.status or "(none)"] += 1
    counts = tally.owner_counts.setdefault(owner.page_id, {"own": 0, "from_districts": 0})
    if owner.via_district:
        tally.via_district += 1
        stats.from_districts += 1
        counts["from_districts"] += 1
    else:
        stats.own += 1
        counts["own"] += 1


def _note_quality(tally: _Tally, stats: report.CountryStats, row: dict[str, Any]) -> None:
    for flag in row["quality_flags"]:
        tally.flags[flag] += 1
        stats.flags[flag] += 1
    group = row["coord_gap"]
    if group is None:
        return
    tally.gap[group] += 1
    stats.gap[group] += 1
    dimensions = (
        ("country", row["country"] or report.NO_COUNTRY),
        ("type", row["kind"]),
        ("city", row["owner_title"] or "(no owner)"),
    )
    for dimension, value in dimensions:
        key = (row["country_iso"] or "", dimension, value)
        counter = tally.gap_rows.setdefault(key, Counter())
        counter["no_coordinates"] += 1
        counter[group] += 1


def _finisher(tally: _Tally, keys: dict[tuple[int, int], str], dups: Any):
    """The last pass over every listing row: its final key and its duplicate fields. Rows arrive
    in (page_id, ordinal) order, so the samples and counts do not depend on the dump's order."""
    sampled: Counter[str] = Counter()

    def finish(row: dict[str, Any]) -> dict[str, Any]:
        pair = (row["page_id"], row["ordinal"])
        out = {**row, "listing_key": keys[pair]}
        stats = tally.country(row["country"], row["country_iso"])
        if pair in dups.duplicates:
            original, basis = dups.duplicates[pair]
            out.update(duplicate_of=keys[original], duplicate_basis=basis)
            out["quality_flags"] = [*row["quality_flags"], "duplicate"]
            for counter in (tally.flags, stats.flags):
                counter["duplicate"] += 1
            tally.dup_basis[basis] += 1
            stats.dup_basis[basis] += 1
            if sampled[basis] < DUPLICATE_SAMPLES:
                sampled[basis] += 1
                tally.dup_samples.append(
                    [
                        basis,
                        row["owner_title"],
                        row["kind"],
                        row["name"],
                        keys[pair],
                        keys[original],
                    ]
                )
        if pair in dups.same_place:
            out["same_place_as"] = keys[dups.same_place[pair]]
            out["quality_flags"] = [*out["quality_flags"], "same_place_as"]
            for counter in (tally.flags, stats.flags):
                counter["same_place_as"] += 1
        return out

    return finish


def _sample_price(tally: _Tally, row: dict[str, Any]) -> None:
    if row["price_status"] in ("unparsed", "partial"):
        reason = f"{row['price_status']}:{row['price_reason']}"
        samples = tally.price_samples.setdefault(reason, [])
        samples.append((row["page_id"], row["ordinal"], row["price"]))
        if len(samples) > 4 * PRICE_SAMPLES:  # keep memory flat: only the smallest page IDs count
            samples.sort()
            del samples[PRICE_SAMPLES:]


def _safe_cell(value: Any) -> Any:
    """A review CSV is opened in a spreadsheet: raw wiki text must not start a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _write_price_samples(target: Path, tally: _Tally, keys: dict[tuple[int, int], str]) -> None:
    """A review sample of prices not fully read: the first rows by page ID, per reason."""
    rows = []
    for reason in sorted(tally.price_samples):
        for page_id, ordinal, text in sorted(tally.price_samples[reason])[:PRICE_SAMPLES]:
            rows.append([reason, page_id, _safe_cell(keys[(page_id, ordinal)]), _safe_cell(text)])
    header = ["reason", "page_id", "listing_key", "price"]
    write_text(target / "prices_unparsed.csv", _csv_text(header, rows))


def _write_reports(
    target: Path,
    totals: dict[str, Any],
    tally: _Tally,
    by_id: dict[int, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
    country_filter: str | None,
    detail_countries: Sequence[str],
    country_names: dict[str, str],
    proving_titles: Sequence[str],
    dump_date: str,
) -> None:
    write_text(
        target / "duplicates.csv",
        _csv_text(
            ["basis", "owner", "kind", "name", "listing_key", "duplicate_of"],
            [
                [_safe_cell(cell) for cell in row]
                for row in sorted(tally.dup_samples, key=lambda r: (r[0], r[4]))
            ],
        ),
    )
    gap_header = ["country_iso", "dimension", "value", "no_coordinates", *report.GAP_ORDER]
    gap_body = [
        [iso, dimension, _safe_cell(value), c["no_coordinates"], *(c[g] for g in report.GAP_ORDER)]
        for (iso, dimension, value), c in sorted(tally.gap_rows.items())
    ]
    write_text(target / "wikidata_gap.csv", _csv_text(gap_header, gap_body))
    failures = sorted(tally.failures)
    write_text(target / "parse_failures.csv", _csv_text(["page_id", "title", "error"], failures))
    header, rows = report.counts_by_country_rows(tally.stats)
    write_text(target / "counts_by_country.csv", _csv_text(header, rows))
    by_type_rows = [[k, totals["by_kind"].get(k, 0)] for k in report.KIND_ORDER]
    write_text(target / "counts_by_type.csv", _csv_text(["type", "listings"], by_type_rows))
    largest = sorted(
        (by_id[page_id]["title"], c["own"], c["from_districts"])
        for page_id, c in tally.owner_counts.items()
    )
    largest.sort(key=lambda item: -(item[1] + item[2]))
    text = report.render_report(totals, tally.stats, largest[:5], dump_date=dump_date)
    write_text(target / "report.md", text)

    for iso in detail_countries:
        # The code was checked at the start, so a country with no listings gets an empty report.
        match = next(
            (s for s in tally.stats.values() if s.iso == iso.upper()),
            report.CountryStats(country_names[iso.upper()], iso.upper()),
        )
        detail = report.render_detail(iso.upper(), match, dump_date=dump_date)
        write_text(target / f"detail-{iso.upper()}.md", detail)

    proving: list[list[Any]] = []
    for title in proving_titles:
        article = by_title.get(title)
        if article is None:
            proving.append([title, "not in the dump"])
        elif country_filter and article["tree_country_iso"] != country_filter:
            proving.append([title, "outside the country filter"])
        else:
            counts = tally.owner_counts.get(
                article["wikivoyage_page_id"], {"own": 0, "from_districts": 0}
            )
            proving.append([title, counts["own"], counts["from_districts"]])
    write_text(target / "proving-batch.md", report.render_proving(proving))
