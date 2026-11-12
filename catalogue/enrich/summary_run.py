"""`wikipedia-summary` (B4b): a short credited Wikipedia summary for destinations and attractions.

It reads the `enrich-wikidata` output (its `destination_facts.jsonl` and `listing_facts.jsonl`,
hash-checked against that run's manifest) and takes the English Wikipedia article that Wikidata
names for each subject: every article, and every see/do listing that has one. Each distinct title
is fetched once, however many subjects share it. Local files only, written to
`out/summaries/<date>[-CC]/`.

Nothing is dropped, rewritten or guessed. A page that is missing, a disambiguation page, an empty
lead, a very short lead and a redirect each get a flag; text is stored only when the API returned
some, and always with its links, licence and attribution (CC BY-SA 4.0).
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.enrich.inputs import _rows, _sha256
from catalogue.enrich.summary import (
    LICENCE,
    LICENCE_URL,
    PageSummary,
    SummaryRules,
    article_url,
    attribution,
    fetch_summaries,
    first_paragraph,
    history_url,
    load_summary_rules,
    permalink,
)
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import parse_dump_date

SCHEMA_VERSION = 1
LISTING_KINDS = frozenset({"see", "do"})
NO_COUNTRY = "(none)"
INPUT_FILES = ("destination_facts.jsonl", "listing_facts.jsonl")


@dataclass(frozen=True, slots=True)
class Subject:
    page_id: int
    ordinal: int  # 0 for an article, the listing's ordinal for a listing
    kind: str  # article or listing
    listing_key: str | None
    country_iso: str | None
    wikidata_id: str | None
    title: str  # the English Wikipedia title Wikidata names


@dataclass(frozen=True, slots=True)
class SummaryResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        totals = self.totals
        flagged = sum(totals["flags"].values())
        lines = [
            f"summary rows: {totals['rows']} (articles {totals['articles']}, "
            f"listings {totals['listings']}); distinct Wikipedia titles: {totals['titles']}",
            f"with text: {totals['with_text']}; flags: {flagged}",
        ]
        if totals["flags"]:
            lines.append(", ".join(f"{k} {v}" for k, v in totals["flags"].items()))
        return "\n".join(lines)


def _verify(folder: Path, country_filter: str | None) -> tuple[str, dict[str, str]]:
    """Check the enrich output belongs to this run; return its manifest hash and input hashes."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"manifest.json not found in {folder}; run enrich-wikidata first")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded_country = manifest["country_filter"]
        recorded = manifest["outputs_sha256"]
    except (ValueError, KeyError, TypeError):
        raise PipelineError(
            "the enrich-wikidata manifest.json is damaged; run enrich-wikidata again"
        ) from None
    if recorded_country != country_filter:
        raise PipelineError(
            "enrich-wikidata was run for a different country filter; use the same --country"
        )
    hashes: dict[str, str] = {}
    for name in INPUT_FILES:
        path = folder / name
        if not path.is_file():
            raise PipelineError(f"{name} not found in {folder}")
        hashes[name] = _sha256(path)
        if hashes[name] != recorded.get(name):
            raise PipelineError(f"{name} does not match the enrich-wikidata manifest")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest(), hashes


def _subjects(folder: Path) -> list[Subject]:
    subjects: list[Subject] = []
    for row in _rows(folder / "destination_facts.jsonl"):
        try:
            page_id = row["wikivoyage_page_id"]
        except KeyError:
            raise PipelineError("destination_facts.jsonl has a damaged row") from None
        title = row.get("enwiki_title")
        if isinstance(title, str) and title:
            subjects.append(
                Subject(
                    page_id,
                    0,
                    "article",
                    None,
                    row.get("tree_country_iso"),
                    row.get("wikidata_id"),
                    title,
                )
            )
    for row in _rows(folder / "listing_facts.jsonl"):
        try:
            page_id, ordinal, key = row["page_id"], row["ordinal"], row["listing_key"]
            kind = row["listing_kind"]
        except KeyError:
            raise PipelineError("listing_facts.jsonl has a damaged row") from None
        title = row.get("enwiki_title")
        if kind in LISTING_KINDS and isinstance(title, str) and title:
            subjects.append(
                Subject(
                    page_id,
                    ordinal,
                    "listing",
                    key,
                    row.get("country_iso"),
                    row.get("wikidata_id"),
                    title,
                )
            )
    return sorted(subjects, key=lambda s: (s.page_id, s.ordinal))


def _row(
    subject: Subject, page: PageSummary, rules: SummaryRules, dump_date: str, read_at: str
) -> dict[str, Any]:
    flags: list[str] = []
    lead = None
    if page.status == "missing":
        flags.append("missing_page")
    elif page.status == "no_answer":
        flags.append("no_answer")
    elif page.status == "disambiguation":
        flags.append("disambiguation")
    elif not page.extract:
        flags.append("empty_extract")
    else:
        lead = page.extract
        if len(lead) < rules.very_short_chars:
            flags.append("very_short")
    if page.redirected:
        flags.append("redirected")
    title = page.title
    return {
        "schema_version": SCHEMA_VERSION,
        "subject": subject.kind,
        "page_id": subject.page_id,
        "ordinal": subject.ordinal,
        "listing_key": subject.listing_key,
        "country_iso": subject.country_iso,
        "wikidata_id": subject.wikidata_id,
        "requested_title": subject.title,
        "wikipedia_title": title,
        "url": article_url(title) if title else None,
        "permalink": permalink(title, page.revision_id) if title and page.revision_id else None,
        "history_url": history_url(title) if title else None,
        "revision_id": page.revision_id,
        "revision_timestamp": page.timestamp,
        "lead_text": lead,
        "summary": first_paragraph(lead) if lead else None,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": attribution(title) if title else None,
        "dump_date": f"{dump_date[:4]}-{dump_date[4:6]}-{dump_date[6:]}",
        "read_at": read_at,
        "flags": sorted(flags),
    }


def _percentile(values: list[int], share: float) -> int:
    return values[min(len(values) - 1, int(share * (len(values) - 1)))]


def _length_stats(lengths: list[int]) -> dict[str, int]:
    if not lengths:
        return {}
    ordered = sorted(lengths)
    return {
        "min": ordered[0],
        "p10": _percentile(ordered, 0.10),
        "median": _percentile(ordered, 0.50),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
    }


def _report(totals: Mapping[str, Any], dump_date: str) -> str:
    lines = [
        f"# wikipedia-summary, dump {dump_date}",
        "",
        f"- rows: {totals['rows']} (articles {totals['articles']}, listings {totals['listings']})",
        f"- distinct Wikipedia titles fetched: {totals['titles']}",
        f"- rows with text: {totals['with_text']}",
        "",
        "## Flags",
        "",
    ]
    lines += [f"- `{flag}`: {count}" for flag, count in totals["flags"].items()] or ["- none"]
    lines += ["", "## Lead length (characters, rows with text)", ""]
    stats = totals["lead_length"]
    if stats:
        lines.append(
            f"min {stats['min']}, p10 {stats['p10']}, median {stats['median']}, "
            f"p90 {stats['p90']}, max {stats['max']}"
        )
    else:
        lines.append("no text")
    lines += [
        "",
        "The `very_short` limit is in `config/summary_rules.toml`; these lengths are what it is "
        "checked against.",
        "",
    ]
    return "\n".join(lines)


def run_wikipedia_summary(
    client: PoliteClient,
    *,
    enrich_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: SummaryRules | None = None,
) -> SummaryResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_summary_rules()
    country_filter = country_iso.upper() if country_iso else None
    manifest_sha, input_hashes = _verify(enrich_folder, country_filter)
    subjects = _subjects(enrich_folder)

    pages = fetch_summaries(client, {s.title for s in subjects})
    read_at = today.isoformat()
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)

    counts: dict[tuple[str, str], Counter] = {}
    flag_counts: Counter = Counter()
    by_country_flag: Counter = Counter()
    lengths: list[int] = []
    with JsonlWriter(target / "summaries.jsonl") as out:
        for subject in subjects:
            row = _row(subject, pages[subject.title], rules, dump_date, read_at)
            out.write(row)
            country = subject.country_iso or NO_COUNTRY
            tally = counts.setdefault((country, f"{subject.kind}s"), Counter())
            tally["rows"] += 1
            tally["with_text"] += row["lead_text"] is not None
            tally["flagged"] += bool(row["flags"])
            if row["lead_text"] is not None:
                lengths.append(len(row["lead_text"]))
            for flag in row["flags"]:
                flag_counts[flag] += 1
                by_country_flag[(country, f"{subject.kind}s", flag)] += 1

    write_text(
        target / "summaries_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "rows", "with_text", "flagged"],
            [
                [c, s, t["rows"], t["with_text"], t["flagged"]]
                for (c, s), t in sorted(counts.items())
            ],
        ),
    )
    write_text(
        target / "flags_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "flag", "count"],
            [[c, s, f, n] for (c, s, f), n in sorted(by_country_flag.items())],
        ),
    )
    totals = {
        "rows": len(subjects),
        "articles": sum(s.kind == "article" for s in subjects),
        "listings": sum(s.kind == "listing" for s in subjects),
        "titles": len({s.title for s in subjects}),
        "with_text": len(lengths),
        "flags": dict(sorted(flag_counts.items())),
        "lead_length": _length_stats(lengths),
    }
    write_text(target / "report.md", _report(totals, dump_date))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "summary_rules_version": rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "enrich_manifest_sha256": manifest_sha,
            "inputs_sha256": input_hashes,
            "totals": totals,
            "outputs_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(target.iterdir())
                if path.is_file() and path.name != "manifest.json"
            },
        },
    )
    return SummaryResult(target, totals)
