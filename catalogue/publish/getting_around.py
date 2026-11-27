"""The getting_around field: a credited excerpt of the Wikivoyage "Get around" section and the
ride apps whose own site lists the city (names and official links from config/ride_apps.toml, no
logos, no fares)."""

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError
from catalogue.publish.hero import CONTENT_PATH

RIDE_APPS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "ride_apps.toml"
MARKUP = re.compile(r"\[\[|\]\]|\{\{|\}\}|<[^>]*>|''")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
PARAGRAPH_END = re.compile(r"[.!?][\"'”’)]*$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
COVERAGE = ("cities", "nationwide", "unknown")


@dataclass(frozen=True, slots=True)
class ContentRules:
    max_chars: int
    min_paragraph_chars: int


def load_content_rules(path: Path = CONTENT_PATH) -> ContentRules:
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))["getting_around"]
        values = (table["max_chars"], table["min_paragraph_chars"])
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise PipelineError(f"{path.name} is missing or damaged") from None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values):
        raise PipelineError(f"{path.name}: [getting_around] values must be positive whole numbers")
    return ContentRules(*values)


def _https(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://")


def _app(app: Any) -> dict[str, Any] | None:
    """One checked app entry, or None when anything is missing or of the wrong shape."""
    if not isinstance(app, dict):
        return None
    coverage, cities = app.get("coverage"), app.get("cities")
    required = {"name", "url", "source_url", "checked", "coverage"}
    if coverage == "cities":
        if not (
            isinstance(cities, list)
            and cities
            and all(isinstance(c, str) and SLUG.fullmatch(c) for c in cities)
        ):
            return None
        required.add("cities")
    if (
        set(app) != required
        or coverage not in COVERAGE
        or not isinstance(app["name"], str)
        or not app["name"].strip()
        or not _https(app["url"])
        or not _https(app["source_url"])
        or type(app["checked"]) is not date  # a TOML date, not a date-time or a string
    ):
        return None
    return {**app, "name": app["name"].strip(), "cities": frozenset(cities or ())}


def load_ride_apps(path: Path = RIDE_APPS_PATH) -> dict[str, dict[str, Any]]:
    """Country code to {"apps": [checked app entries], "note"}; checked so a typo cannot publish.
    Which apps a city shows is `apps_for`."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} is missing or damaged") from None
    found: dict[str, dict[str, Any]] = {}
    for country, entry in table.items():
        apps = entry.get("apps") if isinstance(entry, dict) else None
        note = entry.get("note") if isinstance(entry, dict) else None
        checked = [_app(a) for a in apps] if isinstance(apps, list) else []
        if (
            not re.fullmatch(r"[A-Z]{2}", country)
            or not isinstance(note, str)
            or not note.strip()
            or not checked
            or None in checked
        ):
            raise PipelineError(
                f"{path.name}: [{country}] needs a note and apps with a name, an https url and "
                "source_url, a checked date and a coverage (cities with catalogue slugs, "
                "nationwide, or unknown)"
            )
        found[country] = {"apps": checked, "note": note.strip()}
    return found


def apps_for(
    table: dict[str, dict[str, Any]], country_iso: str | None, slug: str
) -> dict[str, Any] | None:
    """The apps whose own site lists this city or says it serves the whole country, as published
    ({"apps": [{"name", "url"}], "note"}); None when there are none (unknown is never shown)."""
    entry = table.get(country_iso or "")
    if entry is None:
        return None
    shown = [
        {"name": a["name"], "url": a["url"]}
        for a in entry["apps"]
        if a["coverage"] == "nationwide" or (a["coverage"] == "cities" and slug in a["cities"])
    ]
    return {"apps": shown, "note": entry["note"]} if shown else None


def _paragraphs(text: str) -> list[str]:
    found = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        flat = " ".join(block.split())
        if flat:
            found.append(flat)
    return found


def _cut(paragraph: str, limit: int) -> str:
    """At the last sentence end within the limit; else at the last word, with an ellipsis."""
    head = paragraph[:limit]
    ends = [m.end() for m in SENTENCE_END.finditer(head)]
    if ends and ends[-1] >= limit // 3:
        return head[: ends[-1]]
    return head[: head.rfind(" ")].rstrip(" ,;:") + "…" if " " in head else head + "…"


def _complete(paragraph: str) -> str:
    """A paragraph that has a sentence end but trails off after it keeps its complete sentences
    (a cut-off or vandalised tail, like "... by AMTS.UTTAR PRADESH"). Without any sentence end it
    is left as it is, for the length rule to judge."""
    if PARAGRAPH_END.search(paragraph):
        return paragraph
    ends = [m.end() for m in SENTENCE_END.finditer(paragraph)]
    return paragraph[: ends[-1]] if ends else paragraph


def excerpt_of(text: str, rules: ContentRules) -> str | None:
    """Whole paragraphs from the start of a section, up to the limit; None when nothing usable."""
    usable = [
        p
        for p in map(_complete, _paragraphs(text))
        if not MARKUP.search(p)
        and (len(p) >= rules.min_paragraph_chars or p.endswith((".", "!", "?")))
    ]
    if not usable:
        return None
    if len(usable[0]) > rules.max_chars:
        return _cut(usable[0], rules.max_chars)
    taken = [usable[0]]
    for paragraph in usable[1:]:
        if len("\n\n".join([*taken, paragraph])) > rules.max_chars:
            break
        taken.append(paragraph)
    return "\n\n".join(taken)
