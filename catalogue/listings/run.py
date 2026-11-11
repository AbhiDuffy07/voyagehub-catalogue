"""The `fetch-listings` job: dump + discover article list -> listings, sections, markers.

Offline: no network, no database, no settings. It refuses to run unless the dump is the very file
`discover` read (its SHA-1 must equal the one in the discover manifest). Pages are read in dump
order and written as they are parsed, so memory stays small and two runs write identical bytes.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover.dump import sha1_of
from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.discover.parse import DumpPage, iter_pages
from catalogue.discover.slugs import RegistryEntry, load_registry
from catalogue.errors import PipelineError
from catalogue.listings import report
from catalogue.listings.keys import KEY_VERSION, KeyInput, assign_keys, key_slug
from catalogue.listings.output import SCHEMA_VERSION, SortedJsonl, provenance
from catalogue.listings.rollup import Owner, resolve_owner
from catalogue.listings.source import read_articles
from catalogue.listings.wikitext import RawListing, marker_listing, parse_page


# Typed markers on these page types are real places on the rendered page, so they become listings.
MARKER_LISTING_PAGE_TYPES = frozenset({"city", "district", "park"})


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
    counted: list[tuple[RawListing, str]]  # listing, the section it sits under
    owner: Owner | None
    region: str
    marker_listings: int = 0


def _build_page(
    dump_page: DumpPage,
    row: dict[str, Any],
    by_title: dict[str, dict[str, Any]],
    slugs: dict[int, RegistryEntry],
    dump_date: str,
    read_at: str,
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
    }
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
            built.listings.append(
                {
                    **page_fields,
                    **prov,
                    **fields,
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
            built.counted.append((listing, section.key))
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
) -> ListingsResult:
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
        for dump_page in iter_pages(dump_path):
            row = by_id.get(dump_page.page_id)
            if row is None:
                tally.pages_not_in_articles += 1
                continue
            if country_filter and row["tree_country_iso"] != country_filter:
                continue
            try:
                built = _build_page(dump_page, row, by_title, slugs, dump_date, read_at)
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
            for listing, section_key in built.counted:
                _count(
                    tally,
                    stats,
                    listing,
                    section_key,
                    built.region,
                    built.owner,
                    row["role"],
                    today,
                )

        keys = assign_keys(tally.key_inputs)
        listings_out.finish(
            lambda r: {**r, "listing_key": keys[(r["page_id"], r["ordinal"])]},
        )
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
    }
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


def _count(tally, stats, listing, section_key, region, owner, role, today) -> None:
    tally.by_kind[listing.kind] += 1
    tally.by_role[role] += 1
    stats.by_kind[listing.kind] += 1
    stats.by_section[section_key] += 1
    stats.by_region[region] += 1
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
