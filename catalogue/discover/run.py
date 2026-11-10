"""The `discover` job: dump -> tree -> countries -> Wikidata check -> slugs -> local files.

No database is touched (the importer login arrives in B15). Output goes to
`<out_dir>/<dump date>[-<CC>]/`. The slug registry is the committed `data/slugs.json` (the CLI
passes its path): it is the only record of the slugs handed out so far, so it changes only through
a reviewed pull request and is never kept under the gitignored `out/`.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover import output
from catalogue.discover.confirm import Confirmation, confirm_destinations, resolve_countries
from catalogue.discover.dump import sha1_of
from catalogue.discover.parse import Article, parse_dump
from catalogue.discover.slugs import SlugRequest, assign_slugs, load_registry, save_registry
from catalogue.discover.territories import resolve_territories
from catalogue.discover.tree import Assignment, assign_countries, continent_of
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient

CANDIDATE_TYPES = frozenset(output.TYPES)
CANDIDATE_STATUSES = frozenset(output.STATUSES)


@dataclass(frozen=True, slots=True)
class DiscoverResult:
    output_dir: Path
    totals: dict[str, Any]
    counts: list[output.CountRow]

    def summary(self, top: int = 10) -> str:
        totals = self.totals
        kinds = ", ".join(f"{k} {totals['by_type'].get(k, 0)}" for k in output.TYPES)
        states = ", ".join(f"{s} {totals['confirmation'].get(s, 0)}" for s in output.STATES)
        named = [row for row in self.counts if row.country != output.UNASSIGNED]
        ranked = sorted(named, key=lambda row: (-row.total, row.country))[:top]
        lines = [
            f"destinations: {totals['destinations']} ({kinds})",
            f"countries with destinations: {len(named)}",
            f"country check: {states}",
            f"slug events: {totals['slug_events']}",
            f"top {len(ranked)} countries:",
        ]
        lines += [
            f"  {number}. {row.country} ({row.country_iso}): {row.total}"
            for number, row in enumerate(ranked, start=1)
        ]
        return "\n".join(lines)


def _is_candidate(article: Article) -> bool:
    return article.type in CANDIDATE_TYPES and article.status in CANDIDATE_STATUSES


def _count_rows(
    articles: dict[str, Article],
    assignments: dict[str, Assignment],
    confirmations: dict[str, Confirmation],
    iso_by_country: dict[str, str | None],
    in_scope: set[str],
) -> list[output.CountRow]:
    buckets: dict[str, dict[str, Any]] = {}
    for title in sorted(in_scope):
        article = articles[title]
        if article.type not in CANDIDATE_TYPES or article.status is None:
            continue
        country = assignments[title].country or output.UNASSIGNED
        bucket = buckets.setdefault(
            country,
            {"type": Counter(), "status": Counter(), "outline": 0, "state": Counter()},
        )
        if article.status == "outline":
            bucket["outline"] += 1
            continue
        bucket["type"][article.type] += 1
        bucket["status"][article.status] += 1
        bucket["state"][confirmations[title].state] += 1
    return [
        output.CountRow(
            country=country,
            country_iso=iso_by_country.get(country) or "",
            by_type=dict(b["type"]),
            by_status=dict(b["status"]),
            outline=b["outline"],
            by_state=dict(b["state"]),
        )
        for country, b in sorted(buckets.items())
    ]


def run_discover(
    client: PoliteClient,
    *,
    dump_path: Path,
    dump_date: str,
    out_dir: Path,
    registry_path: Path,
    today: date,
    country_iso: str | None = None,
    detail_countries: Sequence[str] = (),
    highlight_titles: Sequence[str] = (),
    country_iso_overrides: Mapping[str, str] | None = None,
) -> DiscoverResult:
    country_filter = country_iso.upper() if country_iso else None
    parsed = parse_dump(dump_path)
    assignments = assign_countries(parsed)
    overrides = country_iso_overrides or {}
    countries = resolve_countries(client, parsed, overrides)
    assignments, countries = resolve_territories(client, assignments, countries)
    iso_by_country = {title: info.iso for title, info in countries.items()}

    def in_filter(title: str) -> bool:
        country = assignments[title].country
        return country_filter is None or (
            country is not None and iso_by_country.get(country) == country_filter
        )

    scope = {t for t, a in parsed.articles.items() if in_filter(t)}
    candidates = sorted(t for t in scope if _is_candidate(parsed.articles[t]))
    confirmations = confirm_destinations(client, candidates, assignments, countries)

    registry, slug_events = assign_slugs(
        [
            SlugRequest(parsed.articles[t].page_id, t, confirmations[t].country_iso)
            for t in candidates
        ],
        load_registry(registry_path),
    )
    save_registry(registry_path, registry)

    records = [
        output.build_record(
            parsed.articles[t],
            assignments[t],
            confirmations[t],
            registry[parsed.articles[t].page_id].slug,
            dump_date=dump_date,
            today=today,
        )
        for t in candidates
    ]
    excluded = sorted(
        (a.title, "ambiguous_status")
        for a in parsed.articles.values()
        if "ambiguous_status" in a.problems and (country_filter is None or a.title in scope)
    )
    counts = _count_rows(parsed.articles, assignments, confirmations, iso_by_country, scope)

    # Every article is kept (continents, countries, regions, itineraries, topics, airports, ...),
    # so nothing is lost for browsing or for later waves.
    article_records = [
        output.build_article_record(
            parsed.articles[t],
            assignments[t],
            tree_country_iso=iso_by_country.get(assignments[t].country or ""),
            continent=continent_of(t, parsed),
            wikidata_id=_wikidata_id(t, confirmations, countries),
            dump_date=dump_date,
            today=today,
        )
        for t in sorted(scope)
    ]

    folder = f"{dump_date}-{country_filter}" if country_filter else dump_date
    target = out_dir / folder
    output.write_destinations(target / "destinations.jsonl", records)
    output.write_jsonl(target / "articles.jsonl", article_records)
    output.write_redirects(target / "redirects.csv", parsed.redirects)
    output.write_counts(target / "counts_by_country.csv", counts)
    output.write_conflicts(target / "conflicts.csv", records)
    output.write_unassigned(target / "unassigned.csv", records)
    output.write_excluded(target / "excluded.csv", excluded)
    output.write_slug_events(target / "slug_events.csv", slug_events)

    for iso in detail_countries:
        _write_detail(
            target,
            iso.upper(),
            records,
            counts,
            parsed.articles,
            assignments,
            iso_by_country,
            highlight_titles,
            dump_date,
        )

    totals = {
        "articles": len(parsed.articles),
        "redirects": len(parsed.redirects),
        "destinations": len(records),
        "by_type": dict(Counter(r["type"] for r in records)),
        "by_status": dict(Counter(r["status"] for r in records)),
        "confirmation": {s: sum(r["confirmation"] == s for r in records) for s in output.STATES},
        "excluded": len(excluded),
        "articles_by_role": dict(Counter(r["role"] for r in article_records)),
        "overrides_applied": sorted(overrides),
        "slug_events": dict(Counter(e.kind for e in slug_events)),
    }
    output.write_manifest(
        target / "manifest.json",
        {
            "schema_version": output.SCHEMA_VERSION,
            "dump_date": dump_date,
            "dump_sha1": sha1_of(dump_path),
            "country_filter": country_filter,
            "generated_on": today.isoformat(),
            "totals": totals,
        },
    )
    return DiscoverResult(output_dir=target, totals=totals, counts=counts)


def _write_detail(
    target: Path,
    iso: str,
    records: list[dict[str, Any]],
    counts: list[output.CountRow],
    articles: dict[str, Article],
    assignments: dict[str, Assignment],
    iso_by_country: dict[str, str | None],
    highlight_titles: Sequence[str],
    dump_date: str,
) -> None:
    mine = [r for r in records if r["country_iso"] == iso or _tree_iso(r, iso_by_country) == iso]
    row = next((c for c in counts if c.country_iso == iso), None)
    if row is None:
        raise PipelineError(f"no destinations found for country {iso}; check the code")
    by_title = {r["title"]: r for r in records}

    highlights = []
    for title in highlight_titles:
        article = articles.get(title)
        if article is None:
            highlights.append({"title": title, "article": None, "record": None})
            continue
        country = assignments[title].country
        if iso_by_country.get(country) != iso:
            continue  # belongs to another country: not part of this report
        highlights.append({"title": title, "article": article, "record": by_title.get(title)})

    text = output.render_detail(
        iso, row.country, mine, row.outline, highlights, dump_date=dump_date
    )
    output.write_text(target / f"detail-{iso}.md", text)


def _tree_iso(record: dict[str, Any], iso_by_country: dict[str, str | None]) -> str | None:
    return iso_by_country.get(record["country"]) if record["country"] else None


def _wikidata_id(
    title: str, confirmations: dict[str, Confirmation], countries: dict[str, Any]
) -> str | None:
    if title in confirmations:
        return confirmations[title].wikidata_id
    return countries[title].qid if title in countries else None
