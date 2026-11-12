"""The review tables, `report.md` and the totals for `enrich-wikidata`.

Everything here is counts and Q-IDs; raw wiki text appears only in `wikipedia_links.csv` (the link
value, escaped so a spreadsheet cannot read it as a formula). The same table the owner reads is the
one the manifest records.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from catalogue.discover.output import _csv_text, write_text
from catalogue.enrich.claims import ItemFacts
from catalogue.enrich.facts import FeeSummary
from catalogue.listings.run import _safe_cell

if TYPE_CHECKING:
    from catalogue.enrich.inputs import ArticleIn
    from catalogue.enrich.items import ItemsResult
    from catalogue.enrich.run import Tally

TOP_COUNTRIES = 20
TOP_UNMAPPED = 300
SAMPLE_LIMIT = 100


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def _sum(tally: "Tally", subject: str, attribute: str) -> int:
    return sum(getattr(c, attribute) for (_, s), c in tally.coords.items() if s == subject)


def _flag_totals(tally: "Tally", subject: str) -> dict[str, int]:
    totals: Counter = Counter()
    for (_, s), count in tally.coords.items():
        if s == subject:
            totals.update(count.flags)
    return dict(sorted(totals.items()))


def _label(items: Mapping[str, ItemFacts], qid: str) -> str:
    item = items.get(qid)
    return item.label if item and item.label else ""


def _fee_rows(tally: "Tally", fees: Mapping[tuple[str, str], FeeSummary]) -> list[list[Any]]:
    countries = sorted({country for country, _ in tally.fee_items})
    rows: list[list[Any]] = []
    for country in countries:
        mine = {k: f for k, f in fees.items() if k[0] == country}
        rows.append(
            [
                country,
                "ALL",
                len(mine),
                sum(f.has_fee for f in mine.values()),
                sum(f.dated for f in mine.values()),
                sum(f.within_two_years for f in mine.values()),
                sum(bool(f.currencies) for f in mine.values()),
            ]
        )
        currencies = sorted({c for f in mine.values() for c in f.currencies})
        for code in currencies:
            sub = [f for f in mine.values() if code in f.currencies]
            rows.append(
                [
                    country,
                    code,
                    "",
                    len(sub),
                    sum(f.dated for f in sub),
                    sum(f.within_two_years for f in sub),
                    len(sub),
                ]
            )
    return rows


def _fee_samples(tally: "Tally", items: Mapping[str, ItemFacts]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for (country, qid), item in sorted(tally.fee_items.items()):
        for fee in item.fees:
            unit = items.get(fee.unit_qid) if fee.unit_qid else None
            rows.append(
                [
                    country,
                    qid,
                    _safe_cell(item.label or ""),
                    fee.amount,
                    fee.unit_qid or "",
                    ";".join(unit.currency_codes) if unit else "",
                    fee.point_in_time or "",
                    fee.start or "",
                    fee.end or "",
                ]
            )
            if len(rows) >= SAMPLE_LIMIT:
                return rows
    return rows


def build_totals(
    *,
    tally: "Tally",
    fees: Mapping[tuple[str, str], FeeSummary],
    articles: Sequence["ArticleIn"],
    listing_count: int,
    listing_rows: int,
    first: "ItemsResult",
    second: "ItemsResult",
) -> dict[str, Any]:
    fee_values = list(fees.values())
    return {
        "articles": len(articles),
        "listings": {
            "read": listing_count,
            "rows": listing_rows,
            "missing_before": _sum(tally, "listings", "missing_before"),
            "filled": _sum(tally, "listings", "filled"),
            "filled_via_wikipedia_link": _sum(tally, "listings", "filled_via_link"),
            "flags": _flag_totals(tally, "listings"),
        },
        "destinations": {
            "missing_before": _sum(tally, "destinations", "missing_before"),
            "filled": _sum(tally, "destinations", "filled"),
            "flags": _flag_totals(tally, "destinations"),
        },
        "wikipedia_links": dict(sorted(tally.link_status.items())),
        "items": {
            "fetched": len(first.items),
            "missing": len(first.missing),
            "merged": len(first.redirects),
            "referenced": len(second.items),
        },
        "unesco": dict(sorted(tally.unesco.items())),
        "timezone_basis": dict(sorted(tally.timezone_basis.items())),
        "p2555": {
            "attractions_with_item": len(fee_values),
            "with_fee": sum(f.has_fee for f in fee_values),
            "dated": sum(f.dated for f in fee_values),
            "within_two_years": sum(f.within_two_years for f in fee_values),
            "with_currency": sum(bool(f.currencies) for f in fee_values),
        },
    }


def summary_text(totals: Mapping[str, Any]) -> str:
    listings, destinations = totals["listings"], totals["destinations"]
    lines = [
        f"articles: {totals['articles']}; listings read: {listings['read']} "
        f"(rows written: {listings['rows']})",
        f"listing coordinates filled: {listings['filled']} of {listings['missing_before']} missing "
        f"({_percent(listings['filled'], listings['missing_before'])}); "
        f"{listings['filled_via_wikipedia_link']} of them through a wikipedia= link",
        f"destination coordinates filled: {destinations['filled']} of "
        f"{destinations['missing_before']} missing "
        f"({_percent(destinations['filled'], destinations['missing_before'])})",
        f"items fetched: {totals['items']['fetched']}; missing: {totals['items']['missing']}; "
        f"merged: {totals['items']['merged']}",
        f"entry fees (P2555) on attractions: {totals['p2555']['with_fee']} of "
        f"{totals['p2555']['attractions_with_item']} with an item; "
        f"{totals['p2555']['dated']} dated, {totals['p2555']['within_two_years']} within 2 years",
    ]
    return "\n".join(lines)


def _coordinate_table(tally: "Tally", countries: Sequence[str] | None = None) -> list[str]:
    lines = [
        "| Subject | Missing before | Filled | Still missing | Filled |",
        "|---|---:|---:|---:|---:|",
    ]
    for subject in ("listings", "destinations"):
        keys = [
            k for k in tally.coords if k[1] == subject and (countries is None or k[0] in countries)
        ]
        missing = sum(tally.coords[k].missing_before for k in keys)
        filled = sum(tally.coords[k].filled for k in keys)
        share = _percent(filled, missing)
        lines.append(f"| {subject} | {missing} | {filled} | {missing - filled} | {share} |")
    return lines


def _flag_lines(totals: Mapping[str, int]) -> list[str]:
    return [f"- `{flag}`: {count}" for flag, count in totals.items()] or ["- none"]


def render_report(
    tally: "Tally", totals: Mapping[str, Any], names: Mapping[str, str], dump_date: str
) -> str:
    by_country = sorted(
        {c for c, _ in tally.coords},
        key=lambda c: (
            -sum(v.filled for (k, _), v in tally.coords.items() if k == c),
            c,
        ),
    )[:TOP_COUNTRIES]
    lines = [f"# enrich-wikidata, dump {dump_date}", "", "## Coordinates, before and after", ""]
    lines += _coordinate_table(tally)
    lines += [
        "",
        "A listing or destination is counted as missing when it has no usable "
        "Wikivoyage point. Wikidata fills it only under the rules in `coords.py`.",
        "",
    ]
    lines += [f"### Top {TOP_COUNTRIES} countries by coordinates filled", ""]
    for country in by_country:
        shown = f"{names.get(country, country)} ({country})"
        lines.append(f"**{shown}**")
        lines += [""] + _coordinate_table(tally, [country]) + [""]
    lines += ["## Flags", "", "Listings:", ""] + _flag_lines(totals["listings"]["flags"])
    lines += ["", "Destinations:", ""] + _flag_lines(totals["destinations"]["flags"])
    lines += ["", "## Wikipedia links", ""] + _flag_lines(totals["wikipedia_links"])
    lines += ["", "## Items", ""] + _flag_lines(totals["items"])
    lines += ["", "## UNESCO", ""] + _flag_lines(totals["unesco"])
    lines += ["", "## Time zones (by basis)", ""] + _flag_lines(totals["timezone_basis"])
    lines += ["", "## Entry fees (P2555), counted only", ""] + _flag_lines(totals["p2555"])
    lines += ["", "No fee value is used anywhere; `p2555_samples.csv` is for review.", ""]
    return "\n".join(lines)


def render_detail(tally: "Tally", country: str, name: str) -> str:
    lines = [f"# enrich-wikidata detail: {name} ({country})", "", "## Coordinates", ""]
    lines += _coordinate_table(tally, [country]) + ["", "## Flags", ""]
    for subject in ("listings", "destinations"):
        count = tally.coords.get((country, subject))
        lines += [f"{subject}:", ""] + _flag_lines(
            dict(sorted(count.flags.items())) if count else {}
        )
        lines.append("")
    return "\n".join(lines)


def write_all(
    target: Path,
    *,
    tally: "Tally",
    fees: Mapping[tuple[str, str], FeeSummary],
    items: Mapping[str, ItemFacts],
    articles: Sequence["ArticleIn"],
    listing_count: int,
    listing_rows: int,
    first: "ItemsResult",
    second: "ItemsResult",
    country_names: Mapping[str, str],
    detail_countries: Sequence[str],
    dump_date: str,
) -> dict[str, Any]:
    totals = build_totals(
        tally=tally,
        fees=fees,
        articles=articles,
        listing_count=listing_count,
        listing_rows=listing_rows,
        first=first,
        second=second,
    )
    coord_rows = [
        [
            country,
            subject,
            c.missing_before,
            c.filled,
            c.filled_via_link,
            c.missing_before - c.filled,
        ]
        for (country, subject), c in sorted(tally.coords.items())
    ]
    write_text(
        target / "coord_fill_by_country.csv",
        _csv_text(
            [
                "country_iso",
                "subject",
                "missing_before",
                "filled",
                "filled_via_wikipedia_link",
                "still_missing",
            ],
            coord_rows,
        ),
    )
    flag_rows = [
        [country, subject, flag, n]
        for (country, subject), c in sorted(tally.coords.items())
        for flag, n in sorted(c.flags.items())
    ]
    write_text(
        target / "flags_by_country.csv",
        _csv_text(["country_iso", "subject", "flag", "count"], flag_rows),
    )
    link_rows = [
        [page_id, ordinal, _safe_cell(key), _safe_cell(value), status, qid]
        for page_id, ordinal, key, value, status, qid in sorted(tally.link_rows)
    ]
    write_text(
        target / "wikipedia_links.csv",
        _csv_text(["page_id", "ordinal", "listing_key", "value", "status", "qid"], link_rows),
    )
    write_text(
        target / "p2555_counts.csv",
        _csv_text(
            [
                "country_iso",
                "currency",
                "items_with_item",
                "items_with_fee",
                "dated",
                "within_two_years",
                "with_currency",
            ],
            _fee_rows(tally, fees),
        ),
    )
    write_text(
        target / "p2555_samples.csv",
        _csv_text(
            [
                "country_iso",
                "wikidata_id",
                "label",
                "amount",
                "unit",
                "currency",
                "point_in_time",
                "start",
                "end",
            ],
            _fee_samples(tally, items),
        ),
    )
    heritage_rows = [
        [country, designation, _safe_cell(_label(items, designation)), len(ids)]
        for (country, designation), ids in sorted(tally.heritage.items())
    ]
    write_text(
        target / "heritage_designations.csv",
        _csv_text(["country_iso", "designation_qid", "label", "items"], heritage_rows),
    )
    unmapped = sorted(tally.unmapped.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_UNMAPPED]
    write_text(
        target / "kind_unmapped.csv",
        _csv_text(
            ["p31_qid", "label", "items"],
            [[qid, _safe_cell(_label(items, qid)), len(ids)] for qid, ids in unmapped],
        ),
    )
    write_text(target / "report.md", render_report(tally, totals, country_names, dump_date))
    for country in detail_countries:
        write_text(
            target / f"detail-{country}.md",
            render_detail(tally, country, country_names.get(country, country)),
        )
    return totals
