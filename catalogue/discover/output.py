"""Write the `discover` output files (local, gitignored, under out/).

The JSONL rows are the contract with the later database loader (B15): a plain reader that
upserts `destinations` on `wikivoyage_page_id`, never changing an existing slug. Bump
`SCHEMA_VERSION` when a field changes meaning. Every row keeps its source, licence and
attribution (CLAUDE.md rule 5).
"""

import csv
import io
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from catalogue.discover.confirm import Confirmation
from catalogue.discover.parse import Article
from catalogue.discover.slugs import SlugEvent
from catalogue.discover.tree import Assignment

SCHEMA_VERSION = 1
LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = "Wikivoyage contributors"
TYPES = ("city", "district", "park")
STATUSES = ("usable", "guide", "star")
STATES = ("confirmed", "unconfirmed", "conflict", "unassigned")
UNASSIGNED = "(unassigned)"


@dataclass(frozen=True, slots=True)
class CountRow:
    country: str
    country_iso: str
    by_type: dict[str, int]
    by_status: dict[str, int]
    outline: int  # same types at outline status: not candidates, kept in the working data
    by_state: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.by_type.values())


def write_text(path: Path, text: str) -> None:
    """Write atomically, so a crash never leaves a half-written file under the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _csv_text(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def source_url(article: Article) -> str:
    title = quote(article.title.replace(" ", "_"), safe="/(),:")
    return f"https://en.wikivoyage.org/w/index.php?title={title}&oldid={article.revision_id}"


def _dashed(compact_date: str) -> str:
    return f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"


def build_record(
    article: Article,
    assignment: Assignment,
    confirmation: Confirmation,
    slug: str,
    *,
    dump_date: str,
    today: date,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "wikivoyage_page_id": article.page_id,
        "slug": slug,
        "title": article.title,
        "wikidata_id": confirmation.wikidata_id,
        "type": article.type,
        "status": article.status,
        "country": assignment.country,
        "country_iso": confirmation.country_iso,
        "confirmation": confirmation.state,
        "confirmation_reason": confirmation.reason,
        "wikidata_country_iso": confirmation.wikidata_country_iso,
        "parent": article.parent,
        "ancestors": list(assignment.ancestors),
        "time_zone_qids": list(confirmation.time_zone_qids),
        "revision_id": article.revision_id,
        "revision_timestamp": article.timestamp,
        "source_url": source_url(article),
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "dump_date": _dashed(dump_date),
        "read_at": today.isoformat(),
    }


def write_destinations(path: Path, records: Iterable[dict[str, Any]]) -> None:
    ordered = sorted(records, key=lambda r: r["wikivoyage_page_id"])
    lines = (json.dumps(r, ensure_ascii=False, sort_keys=True) for r in ordered)
    write_text(path, "".join(f"{line}\n" for line in lines))


def write_counts(path: Path, rows: Sequence[CountRow]) -> None:
    header = ["country", "country_iso", *TYPES, *STATUSES, "total", "outline", *STATES[:3]]
    body = [
        [
            row.country,
            row.country_iso,
            *(row.by_type.get(t, 0) for t in TYPES),
            *(row.by_status.get(s, 0) for s in STATUSES),
            row.total,
            row.outline,
            *(row.by_state.get(s, 0) for s in STATES[:3]),
        ]
        for row in rows
    ]
    write_text(path, _csv_text(header, body))


def write_conflicts(path: Path, records: Iterable[dict[str, Any]]) -> None:
    rows = [
        [r["title"], r["wikidata_id"], r["country"], r["wikidata_country_iso"]]
        for r in records
        if r["confirmation"] == "conflict"
    ]
    write_text(
        path, _csv_text(["title", "wikidata_id", "tree_country", "wikidata_country_iso"], rows)
    )


def write_unassigned(path: Path, records: Iterable[dict[str, Any]]) -> None:
    rows = [
        [r["title"], r["type"], r["status"], r["confirmation_reason"], r["wikidata_country_iso"]]
        for r in records
        if r["confirmation"] == "unassigned"
    ]
    write_text(path, _csv_text(["title", "type", "status", "reason", "wikidata_country_iso"], rows))


def write_excluded(path: Path, rows: Iterable[tuple[str, str]]) -> None:
    write_text(path, _csv_text(["title", "reason"], rows))


def write_slug_events(path: Path, events: Iterable[SlugEvent]) -> None:
    rows = [[e.kind, e.page_id, e.title, e.slug, e.detail] for e in events]
    write_text(path, _csv_text(["kind", "page_id", "title", "slug", "detail"], rows))


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    write_text(path, json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def _region(record: Mapping[str, Any]) -> str:
    """The region directly below the country, or a label when there is none."""
    ancestors = record["ancestors"]
    return ancestors[-2] if len(ancestors) >= 2 else "(directly under the country)"


def _region_path(record: Mapping[str, Any]) -> str:
    return " > ".join(reversed(record["ancestors"][:-1]))


def render_detail(
    iso: str,
    country: str,
    records: Sequence[dict[str, Any]],
    outline: int,
    highlights: Sequence[dict[str, Any]],
    *,
    dump_date: str,
) -> str:
    """One country in detail: type by status, regions, confirmation, and highlighted cities."""
    lines = [
        f"# {country} ({iso}): destinations at usable status or better",
        "",
        f"Wikivoyage dump {_dashed(dump_date)}. Candidates are city, district and park articles at "
        f"usable, guide or star status. {outline} more at outline status stay in the working data.",
        "",
        "## By type and status",
        "",
        "| Type | usable | guide | star | total |",
        "|---|---|---|---|---|",
    ]
    cells = Counter((r["type"], r["status"]) for r in records)
    for kind in TYPES:
        counts = [cells[(kind, s)] for s in STATUSES]
        lines.append(f"| {kind} | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")

    lines += [
        "",
        "## By region",
        "",
        "| Region | city | district | park | total |",
        "|---|---|---|---|---|",
    ]
    by_region: dict[str, Counter[str]] = {}
    for record in records:
        by_region.setdefault(_region(record), Counter())[record["type"]] += 1
    for region, counts in sorted(by_region.items(), key=lambda i: (-sum(i[1].values()), i[0])):
        values = [counts[t] for t in TYPES]
        lines.append(f"| {region} | " + " | ".join(map(str, values)) + f" | {sum(values)} |")

    states = Counter(r["confirmation"] for r in records)
    lines += ["", "## Country check against Wikidata", ""]
    lines += [f"- {state}: {states[state]}" for state in STATES[:3]]
    for record in records:
        if record["confirmation"] == "conflict":
            lines.append(
                f"- conflict: {record['title']} (Wikidata says {record['wikidata_country_iso']})"
            )

    lines += ["", "## Highlighted destinations", ""]
    if highlights:
        lines += [
            "| Destination | Status and type | Region | Country check | Slug |",
            "|---|---|---|---|---|",
        ]
        lines += [_highlight_row(h) for h in highlights]
    else:
        lines.append("None requested.")
    return "\n".join(lines) + "\n"


def _highlight_row(item: dict[str, Any]) -> str:
    if item["record"] is not None:
        record = item["record"]
        return (
            f"| {record['title']} | {record['status']} {record['type']} | {_region_path(record)} "
            f"| {record['confirmation']} | {record['slug']} |"
        )
    if item["article"] is not None:
        article = item["article"]
        what = f"{article.status} {article.type}" if article.type else "no status"
        return f"| {article.title} | {what} | | not a candidate | |"
    return f"| {item['title']} | not in the dump | | | |"
