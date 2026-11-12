"""Counts and Markdown reports for `fetch-listings`. Counts and titles only, never listing text."""

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

NO_COUNTRY = "(no country)"
NO_TYPE = "(no type)"
KIND_ORDER = ("see", "do", "buy", "eat", "drink", "sleep", "go", "listing")
_YEAR = re.compile(r"^(\d{4})")


@dataclass(slots=True)
class CountryStats:
    name: str
    iso: str
    by_kind: Counter[str] = field(default_factory=Counter)
    by_owner_status: Counter[str] = field(default_factory=Counter)
    by_section: Counter[str] = field(default_factory=Counter)
    by_region: Counter[str] = field(default_factory=Counter)
    lastedit_years: Counter[str] = field(default_factory=Counter)
    lastedit_status: Counter[str] = field(default_factory=Counter)
    price_status: Counter[str] = field(default_factory=Counter)
    hours_status: Counter[str] = field(default_factory=Counter)
    price_age: Counter[str] = field(default_factory=Counter)
    hours_age: Counter[str] = field(default_factory=Counter)
    distances: dict[str, list[float]] = field(default_factory=dict)  # page type -> km
    flags: Counter[str] = field(default_factory=Counter)
    dup_basis: Counter[str] = field(default_factory=Counter)
    gap: Counter[str] = field(default_factory=Counter)
    own: int = 0
    from_districts: int = 0
    owner_unresolved: int = 0

    @property
    def total(self) -> int:
        return sum(self.by_kind.values())


def lastedit_bucket(raw: str, *, latest_year: int) -> str:
    """The year of a well-formed `lastedit`, else a label. B3b parses dates properly."""
    if not raw:
        return "(none)"
    match = _YEAR.match(raw)
    if match and 2003 <= int(match.group(1)) <= latest_year:
        return match.group(1)
    return "(unparsed)"


def counts_by_country_rows(stats: Mapping[str, CountryStats]) -> tuple[list[str], list[list[Any]]]:
    header = [
        "country",
        "country_iso",
        *KIND_ORDER,
        "total",
        "own_page",
        "from_districts",
        "owner_unresolved",
    ]
    ordered = sorted(stats.values(), key=lambda s: (s.name == NO_COUNTRY, s.name))
    rows = [
        [
            s.name,
            s.iso,
            *(s.by_kind.get(k, 0) for k in KIND_ORDER),
            s.total,
            s.own,
            s.from_districts,
            s.owner_unresolved,
        ]
        for s in ordered
    ]
    return header, rows


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of a non-empty list (stable, no interpolation)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def distance_lines(by_type: Mapping[str, Sequence[float]]) -> list[str]:
    """How far listings are from their page's own geo, per page type. The thresholds of
    `config/listing_quality.toml` are set from this table, so the reasons can be checked."""
    lines = [
        "## Distance from the page's own geo, by page type",
        "",
        "Kilometres, nearest-rank percentiles, listings with usable coordinates on a page with "
        "a usable `{{geo}}`.",
        "",
        "| Page type | Listings | p50 | p90 | p95 | p99 | p99.9 | max |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for page_type in sorted(by_type, key=lambda k: (k == NO_TYPE, k)):
        values = by_type[page_type]
        if not values:
            continue
        cells = " | ".join(f"{percentile(values, q):.1f}" for q in (50, 90, 95, 99, 99.9, 100))
        lines.append(f"| {page_type} | {len(values)} | {cells} |")
    return [*lines, ""]


GAP_ORDER = ("has_wikidata", "wikipedia_only", "neither")
FLAG_ORDER = (
    "coord_missing",
    "coord_invalid",
    "coord_unchecked",
    "coord_far",
    "name_missing",
    "name_markup",
    "url_invalid",
    "email_invalid",
    "wikidata_invalid",
    "duplicate",
    "same_place_as",
)
GAP_LABELS = {
    "has_wikidata": "has a Wikidata ID",
    "wikipedia_only": "a Wikipedia link only",
    "neither": "neither",
}


def quality_lines(
    flags: Mapping[str, int], dup_basis: Mapping[str, int], gap: Mapping[str, int], total: int
) -> list[str]:
    """Counts of quality flags and of the Wikidata gap. Flagged listings are kept as they are."""
    lines = ["## Quality flags", "", f"Listings checked: {total}. A listing can carry several.", ""]
    lines += ["| Flag | Listings | Share |", "|---|---|---|"]
    for flag in FLAG_ORDER:
        count = flags.get(flag, 0)
        share = f"{100 * count / total:.2f}%" if total else "-"
        lines.append(f"| {flag} | {count} | {share} |")
    if dup_basis:
        detail = ", ".join(f"{k} {dup_basis[k]}" for k in sorted(dup_basis))
        lines += ["", f"Duplicates by basis: {detail}."]
    without = sum(gap.values())
    lines += ["", "## Listings without coordinates: what could fill them in", ""]
    lines += [f"Listings without usable coordinates: {without}.", ""]
    lines += ["| Group | Listings | Share |", "|---|---|---|"]
    for group in GAP_ORDER:
        count = gap.get(group, 0)
        share = f"{100 * count / without:.1f}%" if without else "-"
        lines.append(f"| {GAP_LABELS[group]} | {count} | {share} |")
    return [*lines, ""]


AGE_LABELS = (
    ("within", "within 2 years"),
    ("older", "older"),
    ("unclear", "unclear (the year straddles the cutoff)"),
    ("undated", "undated (missing, unreadable or implausible)"),
)


def freshness_lines(
    price_age: Mapping[str, int],
    hours_age: Mapping[str, int],
    price_status: Mapping[str, int],
    hours_status: Mapping[str, int],
) -> list[str]:
    """The numbers behind the two-year rule for prices and hours (decided later, not here).
    Age is measured from the dump date; a value with no usable `lastedit` is `undated`."""
    lines: list[str] = []
    for title, ages, status in (
        ("Prices", price_age, price_status),
        ("Hours", hours_age, hours_status),
    ):
        total = sum(ages.values())
        lines += [f"## {title}: how old is the last edit", ""]
        lines += [f"Listings with {title.lower()} text: {total}.", ""]
        lines += ["| Last edit | Listings | Share |", "|---|---|---|"]
        for key, label in AGE_LABELS:
            count = ages.get(key, 0)
            share = f"{100 * count / total:.1f}%" if total else "-"
            lines.append(f"| {label} | {count} | {share} |")
        read = ", ".join(f"{k} {status[k]}" for k in sorted(status) if k != "empty")
        lines += ["", f"How well the text was read: {read or 'nothing to read'}.", ""]
    return lines


def render_report(
    totals: Mapping[str, Any],
    stats: Mapping[str, CountryStats],
    largest: Sequence[tuple[str, int, int]],
    *,
    dump_date: str,
) -> str:
    lines = [
        f"# fetch-listings report: Wikivoyage dump {dump_date}",
        "",
        f"- pages read: {totals['pages_read']} "
        f"(not in the article list: {totals['pages_not_in_articles']})",
        f"- listings: {totals['listings']} (from districts, rolled up: {totals['via_district']}; "
        f"no owner: {totals['owner_unresolved']})",
        f"- section texts: {totals['sections']}",
        f"- map markers (not listings): {totals['markers']}",
        "",
        "## Listings by type",
        "",
        "| Type | Listings |",
        "|---|---|",
    ]
    lines += [f"| {k} | {totals['by_kind'].get(k, 0)} |" for k in KIND_ORDER]
    lines += ["", "## Listings by page role", "", "| Role | Listings |", "|---|---|"]
    lines += [f"| {role} | {n} |" for role, n in sorted(totals["by_role"].items())]
    lines += ["", "## Countries with the most listings", "", "| Country | Listings |", "|---|---|"]
    ranked = sorted(stats.values(), key=lambda s: (-s.total, s.name))[:15]
    lines += [f"| {s.name} | {s.total} |" for s in ranked]
    lines += ["", "## Largest owners (checks the 25 MiB Pages file limit later)", ""]
    lines += ["| Owner | Own page | From districts |", "|---|---|---|"]
    lines += [f"| {title} | {own} | {districts} |" for title, own, districts in largest]
    lines.append("")
    lines += freshness_lines(
        totals["price_age"], totals["hours_age"], totals["price_status"], totals["hours_status"]
    )
    lines += quality_lines(
        totals["quality_flags"],
        totals["duplicates_by_basis"],
        totals["wikidata_gap"],
        totals["listings"],
    )
    merged: dict[str, list[float]] = {}
    for country in stats.values():
        for page_type, values in country.distances.items():
            merged.setdefault(page_type, []).extend(values)
    lines += distance_lines(merged)
    return "\n".join(lines).rstrip("\n") + "\n"


def _table(
    lines: list[str],
    title: str,
    heading: str,
    counter: Mapping[str, int],
    order: Sequence[str] = (),
) -> None:
    keys = [k for k in order if k in counter] or sorted(counter, key=lambda k: (-counter[k], k))
    lines.extend([f"## {title}", "", f"| {heading} | Listings |", "|---|---|"])
    lines.extend(f"| {k} | {counter[k]} |" for k in keys)
    lines.append("")


def render_detail(iso: str, stats: CountryStats, *, dump_date: str) -> str:
    lines = [
        f"# {stats.name} ({iso}): listings",
        "",
        f"Wikivoyage dump {dump_date}. {stats.total} listings: {stats.own} on the owner's own "
        f"page, {stats.from_districts} rolled up from district pages, "
        f"{stats.owner_unresolved} with no owner.",
        "",
    ]
    _table(lines, "By type", "Type", stats.by_kind, KIND_ORDER)
    _table(lines, "By region", "Region", stats.by_region)
    _table(lines, "By owner status", "Status", stats.by_owner_status)
    _table(lines, "By section", "Section", stats.by_section)
    _table(lines, "By last edit year", "Year", stats.lastedit_years, sorted(stats.lastedit_years))
    lines += freshness_lines(
        stats.price_age, stats.hours_age, stats.price_status, stats.hours_status
    )
    lines += quality_lines(stats.flags, stats.dup_basis, stats.gap, stats.total)
    lines += distance_lines(stats.distances)
    return "\n".join(lines)


def render_proving(rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "# Proving batch: listings per city",
        "",
        "| City | Own page | From districts | Total |",
        "|---|---|---|---|",
    ]
    for row in rows:
        if len(row) == 2:
            lines.append(f"| {row[0]} | {row[1]} | | |")
        else:
            lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[1] + row[2]} |")
    return "\n".join(lines) + "\n"
