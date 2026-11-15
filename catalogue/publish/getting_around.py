"""The getting_around field: a credited excerpt of the Wikivoyage "Get around" section and the
country's ride apps (names and official links from config/ride_apps.toml, no logos, no fares)."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError
from catalogue.publish.hero import CONTENT_PATH

RIDE_APPS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "ride_apps.toml"
MARKUP = re.compile(r"\[\[|\]\]|\{\{|\}\}|<[^>]*>|''")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


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


def load_ride_apps(path: Path = RIDE_APPS_PATH) -> dict[str, dict[str, Any]]:
    """Country code to {"apps": [{"name", "url"}], "note"}; checked so a typo cannot publish."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} is missing or damaged") from None
    found: dict[str, dict[str, Any]] = {}
    for country, entry in table.items():
        apps = entry.get("apps") if isinstance(entry, dict) else None
        note = entry.get("note") if isinstance(entry, dict) else None
        if (
            not re.fullmatch(r"[A-Z]{2}", country)
            or not isinstance(note, str)
            or not note.strip()
            or not isinstance(apps, list)
            or not apps
            or not all(
                isinstance(a, dict)
                and set(a) == {"name", "url"}
                and isinstance(a["name"], str)
                and a["name"].strip()
                and isinstance(a["url"], str)
                and a["url"].startswith("https://")
                for a in apps
            )
        ):
            raise PipelineError(
                f"{path.name}: [{country}] needs a note and apps with a name and an https url"
            )
        found[country] = {"apps": [dict(a) for a in apps], "note": note.strip()}
    return found


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


def excerpt_of(text: str, rules: ContentRules) -> str | None:
    """Whole paragraphs from the start of a section, up to the limit; None when nothing usable."""
    usable = [
        p
        for p in _paragraphs(text)
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
