"""Stable URL slugs for destinations.

The Wikivoyage page ID is the stable key, because Wikivoyage renames pages. A slug is assigned
once and never changes:
- a renamed page keeps its old slug, and the new title is recorded;
- on a later collision only the newcomer is changed (it gets the country suffix);
- a slug stays reserved even if its page stops being a candidate.

Every collision, rename and fallback is returned as an event so the run can log it.

The registry is a small JSON file that must outlive any single run. A damaged file is an error,
never a silent reset, because a reset could hand out different slugs than the ones already used.
"""

import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from catalogue.errors import PipelineError

REGISTRY_SCHEMA_VERSION = 1
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    slug: str
    title: str  # the page's latest known title


@dataclass(frozen=True, slots=True)
class SlugRequest:
    page_id: int
    title: str
    country_iso: str | None


@dataclass(frozen=True, slots=True)
class SlugEvent:
    kind: str  # collision, rename, fallback
    page_id: int
    title: str
    slug: str
    detail: str


def slugify(title: str) -> str:
    """Lowercase, ASCII-folded, hyphenated. A title with no Latin letters gives an empty slug."""
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return _NOT_ALNUM.sub("-", folded.lower()).strip("-")


def _unique_suffixed(base: str, request: SlugRequest, taken: set[str]) -> str:
    if request.country_iso:
        with_country = f"{base}-{request.country_iso.lower()}"
        if with_country not in taken:
            return with_country
        base = with_country
    candidate = f"{base}-{request.page_id}"
    while candidate in taken:  # only reachable with hand-edited registries
        candidate += "x"
    return candidate


def assign_slugs(
    requests: Iterable[SlugRequest], registry: dict[int, RegistryEntry]
) -> tuple[dict[int, RegistryEntry], list[SlugEvent]]:
    """Return the updated registry (the input is not modified) and the events of this run."""
    updated = dict(registry)
    taken = {entry.slug for entry in registry.values()}
    events: list[SlugEvent] = []

    # Page-ID order makes the result independent of input order: the older page wins a tie.
    for request in sorted(requests, key=lambda r: r.page_id):
        known = updated.get(request.page_id)
        if known is not None:
            if known.title != request.title:
                events.append(
                    SlugEvent(
                        "rename",
                        request.page_id,
                        request.title,
                        known.slug,
                        f"renamed from {known.title!r} to {request.title!r}; slug kept",
                    )
                )
                updated[request.page_id] = RegistryEntry(known.slug, request.title)
            continue

        base = slugify(request.title)
        if not base:
            slug = f"place-{request.page_id}"
            events.append(
                SlugEvent("fallback", request.page_id, request.title, slug, "no Latin letters")
            )
        elif base not in taken:
            slug = base
        else:
            slug = _unique_suffixed(base, request, taken)
            events.append(
                SlugEvent(
                    "collision", request.page_id, request.title, slug, f"{base!r} was already taken"
                )
            )
        taken.add(slug)
        updated[request.page_id] = RegistryEntry(slug, request.title)
    return updated, events


def load_registry(path: Path) -> dict[int, RegistryEntry]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema_version"] != REGISTRY_SCHEMA_VERSION:
            raise ValueError("schema version")
        return {
            int(page_id): RegistryEntry(slug=entry["slug"], title=entry["title"])
            for page_id, entry in data["entries"].items()
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PipelineError(
            "the slug registry file is damaged or from another version; "
            "fix or restore it, do not delete it (that would change published slugs)"
        ) from None


def save_registry(path: Path, registry: dict[int, RegistryEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "entries": {
            str(page_id): {"slug": entry.slug, "title": entry.title}
            for page_id, entry in sorted(registry.items())
        },
    }
    temporary = path.with_suffix(".tmp")
    # LF and a final newline on every OS: the file is committed, so Windows and Linux must write
    # the same bytes or a run on another machine would rewrite every line in the pull request.
    temporary.write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(temporary, path)
