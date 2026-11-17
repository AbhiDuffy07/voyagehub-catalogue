"""The `propose-slugs` job: a slug for every city article that has none.

`discover` hands out slugs to confirmed destinations only. Outline cities (and any other city
article) have no slug, so the static site cannot publish them even when they are ready. This job
adds them to the same committed registry, `data/slugs.json`. It only ever adds: an existing entry
(slug and title) is never touched, so every published URL stays as it is.

Rules, first free name wins; pages are handled in page-ID order so the older page keeps the plain
name and the result never depends on input order:
1. the lowercase ASCII-folded title (`São Paulo` -> `sao-paulo`; titles are the English Wikivoyage
   titles, so a title with no Latin letters at all is rare and falls back to `place-<page id>`);
2. the title plus the region below the country (`aurangabad-bihar`), then the other regions above
   the city, nearest the country first;
3. the title plus the two-letter country code;
4. that plus the page ID (when no country is known, the title plus the page ID).

The registry is written through `save_registry`, so the file keeps LF endings and a final newline.
"""

import json
import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.discover.output import write_text
from catalogue.discover.slugs import RegistryEntry, load_registry, save_registry, slugify
from catalogue.errors import PipelineError

COLLISION_KINDS = ("collision_region", "collision_country", "collision_id")
EXAMPLES = 20
EXAMPLE_COLLISIONS = 5
EXAMPLE_SEED = 20261101


@dataclass(frozen=True, slots=True)
class ProposeRequest:
    page_id: int
    title: str
    country_iso: str | None
    regions: tuple[str, ...]  # region below the country first


@dataclass(frozen=True, slots=True)
class ProposeEvent:
    kind: str  # collision_region, collision_country, collision_id, fallback
    page_id: int
    title: str
    slug: str


def _candidates(base: str, request: ProposeRequest) -> Iterable[tuple[str, str]]:
    """(slug, event kind) in order of preference; the first one not taken is used."""
    for region in request.regions:
        region_slug = slugify(region)
        if region_slug and region_slug != base:
            yield f"{base}-{region_slug}", "collision_region"
    if request.country_iso:
        country = f"{base}-{request.country_iso.lower()}"
        yield country, "collision_country"
        yield f"{country}-{request.page_id}", "collision_id"
    else:
        yield f"{base}-{request.page_id}", "collision_id"


def propose_slugs(
    requests: Iterable[ProposeRequest], registry: dict[int, RegistryEntry]
) -> tuple[dict[int, RegistryEntry], list[ProposeEvent]]:
    """The registry plus a slug for every request that has none. The input is not modified."""
    updated = dict(registry)
    taken = {entry.slug for entry in registry.values()}
    events: list[ProposeEvent] = []
    for request in sorted(requests, key=lambda r: r.page_id):
        if request.page_id in updated:
            continue  # existing slugs never change, nor does their recorded title
        base = slugify(request.title)
        if not base:
            slug, kind = f"place-{request.page_id}", "fallback"
        elif base not in taken:
            slug, kind = base, ""
        else:
            slug, kind = next(
                ((s, k) for s, k in _candidates(base, request) if s not in taken),
                (f"{base}-{request.page_id}", "collision_id"),
            )
            while slug in taken:  # only reachable with hand-edited registries
                slug += "x"
        if kind:
            events.append(ProposeEvent(kind, request.page_id, request.title, slug))
        taken.add(slug)
        updated[request.page_id] = RegistryEntry(slug, request.title)
    return updated, events


def _regions(article: dict[str, Any]) -> tuple[str, ...]:
    """Ancestors without the country, the one directly below the country first."""
    country = article.get("tree_country")
    names = [a for a in article.get("ancestors") or [] if a and a != country]
    parent = article.get("parent")
    if parent and parent != country and parent not in names:
        names.insert(0, parent)  # nearest first, like the ancestors
    ordered = names[::-1]
    own = article.get("title")
    return tuple(dict.fromkeys(name for name in ordered if name != own))


def read_requests(articles_path: Path) -> list[ProposeRequest]:
    if not articles_path.is_file():
        raise PipelineError("the discover articles file does not exist; run discover first")
    requests = []
    try:
        with articles_path.open(encoding="utf-8") as handle:
            for line in handle:
                article = json.loads(line)
                if article["type"] != "city":
                    continue
                iso = article.get("tree_country_iso")
                requests.append(
                    ProposeRequest(
                        page_id=int(article["wikivoyage_page_id"]),
                        title=article["title"],
                        country_iso=iso.upper() if iso else None,
                        regions=_regions(article),
                    )
                )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PipelineError("the discover articles file is damaged; run discover again") from None
    return requests


@dataclass(frozen=True, slots=True)
class ProposeResult:
    report_path: Path
    cities: int
    added: int
    added_by_country: dict[str, int]
    collisions: dict[str, int]
    fallbacks: int
    registry_size: int

    def summary(self) -> str:
        return "\n".join(
            [
                f"city articles: {self.cities}; slugs added: {self.added} "
                f"(India {self.added_by_country.get('IN', 0)}); registry now {self.registry_size}",
                "collisions resolved by region "
                f"{self.collisions['collision_region']}, by country code "
                f"{self.collisions['collision_country']}, by page id "
                f"{self.collisions['collision_id']}; no-Latin fallbacks {self.fallbacks}",
            ]
        )


def _examples(
    added: list[tuple[ProposeRequest, str, str]],
) -> list[tuple[ProposeRequest, str, str]]:
    rng = random.Random(EXAMPLE_SEED)  # noqa: S311 - a reproducible sample, not security
    collided = [a for a in added if a[2].startswith("collision")]
    plain = [a for a in added if not a[2].startswith("collision")]
    chosen = rng.sample(collided, min(EXAMPLE_COLLISIONS, len(collided)))
    chosen += rng.sample(plain, min(EXAMPLES - len(chosen), len(plain)))
    return sorted(chosen, key=lambda a: a[0].page_id)


def _report(result: ProposeResult, examples: list[tuple[ProposeRequest, str, str]]) -> str:
    lines = [
        "# Proposed slugs",
        "",
        result.summary(),
        "",
        f"{len(examples)} seeded random examples ({EXAMPLE_COLLISIONS} collisions when there are "
        "that many):",
        "",
        "| page id | title -> slug | how |",
        "|---|---|---|",
    ]
    lines += [
        f"| {r.page_id} | {r.title} -> {slug} | {kind or 'plain'} |" for r, slug, kind in examples
    ]
    return "\n".join(lines) + "\n"


def run_propose_slugs(*, articles_path: Path, registry_path: Path) -> ProposeResult:
    if not registry_path.is_file():
        raise PipelineError(
            f"the slug registry {registry_path} does not exist; run from the repository folder "
            "or fix --data-dir"
        )
    requests = read_requests(articles_path)
    before = load_registry(registry_path)
    after, events = propose_slugs(requests, before)
    kinds = {e.page_id: e.kind for e in events}
    by_id = {r.page_id: r for r in requests}
    added = [
        (by_id[pid], after[pid].slug, kinds.get(pid, ""))
        for pid in sorted(after.keys() - before.keys())
        if pid in by_id
    ]
    if after != before:
        save_registry(registry_path, after)
    counts = Counter(e.kind for e in events)
    result = ProposeResult(
        report_path=articles_path.parent / "slug_proposals.md",
        cities=len(requests),
        added=len(added),
        added_by_country=dict(Counter(r.country_iso or "unknown" for r, _, _ in added)),
        collisions={kind: counts[kind] for kind in COLLISION_KINDS},
        fallbacks=counts["fallback"],
        registry_size=len(after),
    )
    write_text(result.report_path, _report(result, _examples(added)))
    return result
