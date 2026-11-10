"""Research files built from the `discover` output (no network, no dump, no database).

Reused later for the weekly reports. Reads `articles.jsonl`, `destinations.jsonl` and
`manifest.json` from `<out>/<dump date>/` and writes to its `research/` folder:
- all_articles.csv: every article, counted by type and status;
- countries.csv: every country (and territory), sorted by destinations;
- <country>_destinations.csv: every destination of a country, outline ones too;
- summary.md: plain-language definitions and the headline numbers.
"""

import csv
import io
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from catalogue.discover import output
from catalogue.discover.slugs import slugify
from catalogue.errors import PipelineError

STATUS_ORDER = ("outline", "usable", "guide", "star")
TYPE_ORDER = (
    "continent",
    "country",
    "region",
    "ruralarea",
    "city",
    "district",
    "park",
    "itinerary",
    "topic",
    "phrasebook",
    "airport",
    "station",
    "diveguide",
    "event",
)
NO_TYPE = "(no type)"
NO_REGION = "(directly under the country)"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PipelineError("no discover output found here; run discover first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def page_url(title: str) -> str:
    return "https://en.wikivoyage.org/wiki/" + quote(title.replace(" ", "_"), safe="/(),:")


def _all_articles(articles: Sequence[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    cells = Counter((a["type"] or NO_TYPE, a["status"] or "no_status") for a in articles)
    present = {kind for kind, _ in cells}
    kinds = [k for k in TYPE_ORDER if k in present]
    kinds += sorted(present - set(TYPE_ORDER) - {NO_TYPE})
    kinds += [NO_TYPE] if NO_TYPE in present else []
    columns = [*STATUS_ORDER, "no_status"]
    header = ["type", *columns, "total"]
    body = [
        [k, *(cells[(k, c)] for c in columns), sum(cells[(k, c)] for c in columns)] for k in kinds
    ]
    sums = [sum(row[i + 1] for row in body) for i in range(len(columns))]
    body.append(["all types", *sums, len(articles)])
    return header, body


def _countries(articles: Sequence[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    kinds = output.TYPES
    names = {a["title"] for a in articles if a["type"] == "country"}
    names |= {a["tree_country"] for a in articles if a["tree_country"]}
    by_title = {a["title"]: a for a in articles}

    def iso_of(name: str) -> str:
        own = by_title.get(name)
        if own and own["tree_country_iso"]:
            return own["tree_country_iso"]
        codes = (a["tree_country_iso"] for a in articles if a["tree_country"] == name)
        return next((code for code in codes if code), "")

    counts: dict[str, Counter[Any]] = {}
    for a in articles:
        if a["type"] in kinds and a["status"] is not None:
            bucket = counts.setdefault(a["tree_country"] or output.UNASSIGNED, Counter())
            if a["status"] == "outline":
                bucket["outline"] += 1
            else:
                bucket[(a["type"], a["status"])] += 1
                bucket["total"] += 1

    header = ["country", "country_iso", "continent"]
    header += [f"{k}_{s}" for k in kinds for s in output.STATUSES] + ["outline", "total"]
    rows = []
    for name in sorted(names | ({output.UNASSIGNED} if output.UNASSIGNED in counts else set())):
        bucket = counts.get(name, Counter())
        own = by_title.get(name)
        unassigned = name == output.UNASSIGNED
        rows.append(
            [
                name,
                "" if unassigned else iso_of(name),
                "" if unassigned or not own else (own["continent"] or ""),
                *(bucket[(k, s)] for k in kinds for s in output.STATUSES),
                bucket["outline"],
                bucket["total"],
            ]
        )
    rows.sort(key=lambda r: (r[0] == output.UNASSIGNED, -r[-1], r[0]))
    return header, rows


def _country_destinations(
    articles: Sequence[dict[str, Any]], iso: str
) -> tuple[str, list[list[Any]]]:
    mine = [
        a
        for a in articles
        if a["tree_country_iso"] == iso and a["type"] in output.TYPES and a["status"] is not None
    ]
    if not mine:
        raise PipelineError(f"no destinations found for country {iso}; check the code")
    country = mine[0]["tree_country"]
    rows = []
    for a in mine:
        ancestors = a["ancestors"]
        region = ancestors[-2] if len(ancestors) >= 2 else NO_REGION
        path = " > ".join(reversed(ancestors[:-1])) or NO_REGION
        rows.append([a["title"], region, path, a["type"], a["status"], page_url(a["title"])])
    rows.sort(key=lambda r: (r[1], r[3], r[0]))
    return country, rows


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    output.write_text(path, buffer.getvalue())


DEFINITIONS = """\
## What the words mean

- **Article**: a normal Wikivoyage page (not a redirect, talk page or template).
- **Type**: what the article is about, read from its status template. For example
  `{{usablecity}}` means a city. Types: continent, country, region, city, district, park,
  itinerary, topic, phrasebook, airport and a few more.
- **Status**: how complete the article is: *outline* (a skeleton), *usable*, *guide* or *star*
  (the best). Usable, guide and star are the levels this catalogue publishes from.
- **Candidate destination**: a city, district or park at usable, guide or star status.
- **Outline destination**: the same types at outline status. Kept in the working data, and
  published only if they later pass the readiness checks.
- **Country of a page**: found by walking up the page's parent chain (IsPartOf) to the nearest
  country. A territory with its own ISO 3166-1 code counts as its own country.
- **Confirmed**: Wikidata names the same country. **Unconfirmed**: Wikidata cannot say.
  **Conflict**: Wikidata names a different country (disputed places such as Sevastopol). The
  page gets no country and is not published. **Unassigned**: the parent chain does not lead to
  any country or territory.
- **Continent**: the nearest continent page above the country in the same parent chain. It is
  left empty, never guessed, when the chain does not reach one.
"""


def _summary(
    articles: Sequence[dict[str, Any]],
    destinations: Sequence[dict[str, Any]],
    manifest: Mapping[str, Any],
    category_totals: Mapping[str, int] | None,
) -> str:
    dump_date = manifest.get("dump_date", "unknown")
    redirects = manifest.get("totals", {}).get("redirects", "unknown")
    by_status = Counter(a["status"] for a in articles if a["status"])
    by_role = Counter(a["role"] for a in articles)
    states = Counter(d["confirmation"] for d in destinations)
    kinds = Counter(d["type"] for d in destinations)
    countries = {d["country_iso"] for d in destinations if d["country_iso"]}
    lines = [
        f"# Discover research summary (Wikivoyage dump {dump_date})",
        "",
        "## Headline numbers",
        "",
        f"- Articles in the dump: {len(articles)} (redirects are not counted: {redirects})",
        f"- Candidate destinations: {len(destinations)} "
        f"(cities {kinds['city']}, districts {kinds['district']}, parks {kinds['park']})",
        f"- Countries and territories with a country code and destinations: {len(countries)}",
        "- Country check against Wikidata: " + ", ".join(f"{s} {states[s]}" for s in output.STATES),
        f"- Outline cities, districts and parks kept in the working data: "
        f"{by_role['destination_outline']}",
        "- Pages kept, by role: " + ", ".join(f"{r} {n}" for r, n in sorted(by_role.items())),
        "",
        "## Pages by status",
        "",
    ]
    if category_totals:
        lines += [
            "| Status | This run (all types) | Wikivoyage category | Difference |",
            "|---|---|---|---|",
        ]
        for status in ("usable", "guide", "star"):
            if status in category_totals:
                ours, theirs = by_status[status], category_totals[status]
                diff = "0" if ours == theirs else f"{ours - theirs:+d}"
                lines.append(f"| {status} | {ours} | {theirs} | {diff} |")
    else:
        lines += [f"- {s}: {by_status[s]}" for s in STATUS_ORDER]
    return "\n".join([*lines, "", DEFINITIONS])


def write_research(
    dump_dir: Path,
    *,
    detail_countries: Sequence[str] = (),
    category_totals: Mapping[str, int] | None = None,
) -> Path:
    articles = _read_jsonl(dump_dir / "articles.jsonl")
    destinations = _read_jsonl(dump_dir / "destinations.jsonl")
    manifest_path = dump_dir / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    folder = dump_dir / "research"
    _write_csv(folder / "all_articles.csv", *_all_articles(articles))
    _write_csv(folder / "countries.csv", *_countries(articles))
    for iso in detail_countries:
        country, rows = _country_destinations(articles, iso.upper())
        header = ["name", "region", "region_path", "type", "status", "link"]
        _write_csv(folder / f"{slugify(country)}_destinations.csv", header, rows)
    summary = _summary(articles, destinations, manifest, category_totals)
    output.write_text(folder / "summary.md", summary)
    return folder
