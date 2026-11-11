"""Stream-parse the Wikivoyage dump into articles and redirects (standard library only).

Only the facts `discover` needs are kept: the page and revision IDs, the type and status from
the status template (for example `{{usablecity}}`), and the `{{IsPartOf|...}}` parent. Anything
unclear is flagged with a short reason code and left empty, never guessed.
"""

import bz2
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

STATUSES = ("outline", "usable", "guide", "star")
# The types seen in the 2026-11-01 dump. An unknown suffix is ignored, so an unrelated template
# such as {{guidelines}} can never become a "type".
TYPES = frozenset(
    {
        "airport",
        "city",
        "continent",
        "country",
        "district",
        "diveguide",
        "event",
        "itinerary",
        "park",
        "phrasebook",
        "region",
        "ruralarea",
        "station",
        "topic",
    }
)

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z]+)\s*(?:\|[^{}]*)?\}\}")
_ISPARTOF = re.compile(r"\{\{\s*IsPartOf\s*\|([^{}]*)\}\}", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Article:
    page_id: int
    title: str
    revision_id: int
    timestamp: str
    type: str | None
    status: str | None
    parent: str | None
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedDump:
    articles: dict[str, Article]
    redirects: dict[str, str]


def normalize_title(title: str) -> str:
    """MediaWiki treats underscores as spaces and ignores the case of the first letter."""
    tidy = _SPACES.sub(" ", title.replace("_", " ")).strip()
    return tidy[:1].upper() + tidy[1:]


def classify(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (type, status, problem). Two different statuses on one page are not guessed."""
    found: set[tuple[str, str]] = set()
    for match in _TEMPLATE.finditer(_COMMENT.sub("", text)):
        name = match.group(1).lower()
        for status in STATUSES:
            kind = name.removeprefix(status)
            if name.startswith(status) and kind in TYPES:
                found.add((kind, status))
    if len(found) > 1:
        return None, None, "ambiguous_status"
    if found:
        kind, status = next(iter(found))
        return kind, status, None
    return None, None, None


def extract_parent(text: str) -> tuple[str | None, str | None]:
    """Return (parent, problem). The last IsPartOf tag wins; a piped argument is not guessed."""
    tags = _ISPARTOF.findall(_COMMENT.sub("", text))
    if not tags:
        return None, None
    argument = tags[-1]
    if "|" in argument or not argument.strip():
        return None, "unparsed_parent"
    return normalize_title(argument), None


def _text(page: ET.Element, path: str) -> str:
    element = page.find(path)
    return (element.text or "") if element is not None else ""


@dataclass(frozen=True, slots=True)
class DumpPage:
    """One article page (namespace 0, not a redirect) as it stands in the dump."""

    page_id: int
    title: str
    revision_id: int
    timestamp: str
    text: str


def _stream_pages(path: Path) -> Iterator[ET.Element]:
    """Yield each namespace-0 page element; the caller must finish with it before the next."""
    with bz2.open(path, "rb") as stream:
        # S314: the file is the Wikimedia dump, already checked against its published SHA-1.
        # defusedxml would be a new dependency, which needs the owner's approval first.
        for _, element in ET.iterparse(stream):  # noqa: S314
            if element.tag.rsplit("}", 1)[-1] != "page":
                continue
            if _text(element, "{*}ns") == "0":
                yield element
            element.clear()


def iter_pages(path: Path) -> Iterator[DumpPage]:
    """Stream the article pages in dump order (redirects and other namespaces are skipped)."""
    for element in _stream_pages(path):
        if element.find("{*}redirect") is not None:
            continue
        yield DumpPage(
            page_id=int(_text(element, "{*}id")),
            title=normalize_title(_text(element, "{*}title")),
            revision_id=int(_text(element, "{*}revision/{*}id")),
            timestamp=_text(element, "{*}revision/{*}timestamp"),
            text=_text(element, "{*}revision/{*}text"),
        )


def parse_dump(path: Path) -> ParsedDump:
    articles: dict[str, Article] = {}
    redirects: dict[str, str] = {}
    for element in _stream_pages(path):
        title = normalize_title(_text(element, "{*}title"))
        redirect = element.find("{*}redirect")
        if redirect is not None:
            redirects[title] = normalize_title(redirect.get("title", ""))
        else:
            articles[title] = _article(element, title)
    return ParsedDump(articles=articles, redirects=redirects)


def _article(page: ET.Element, title: str) -> Article:
    text = _text(page, "{*}revision/{*}text")
    kind, status, status_problem = classify(text)
    parent, parent_problem = extract_parent(text)
    problems = tuple(p for p in (status_problem, parent_problem) if p)
    return Article(
        page_id=int(_text(page, "{*}id")),
        title=title,
        revision_id=int(_text(page, "{*}revision/{*}id")),
        timestamp=_text(page, "{*}revision/{*}timestamp"),
        type=kind,
        status=status,
        parent=parent,
        problems=problems,
    )
