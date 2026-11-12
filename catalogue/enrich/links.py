"""Turn Wikivoyage `wikipedia=` values into Wikidata IDs (English Wikipedia only).

A value is accepted as a plain title, `en:Title`, or an `en.wikipedia.org/wiki/Title` URL. Anything
else gets a status that says why it was not resolved, and no ID. Nothing is guessed: a
disambiguation page, a missing page, a link to a section, another language edition or a page
without a Wikidata item never yields an ID.

Statuses: resolved, resolved_via_redirect, missing, disambiguation, no_item, section_link,
other_language, bad_value, no_answer (the server's reply did not mention the title).
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.parse import unquote

from catalogue.enrich.api import api_get
from catalogue.http import PoliteClient

ENWIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 50
MAX_TITLE_LENGTH = 255
RESOLVED = frozenset({"resolved", "resolved_via_redirect"})

_URL = re.compile(r"https?://([a-z0-9-]+)(?:\.m)?\.wikipedia\.org/wiki/(.+)", re.IGNORECASE)
_LANGUAGE_PREFIX = re.compile(r"([A-Za-z]{2,3}):(\S.*)")
_ILLEGAL = re.compile(r"[|\[\]{}<>\x00-\x1f\x7f]")
_QID = re.compile(r"Q[1-9][0-9]*")
_MAX_REDIRECT_HOPS = 5


@dataclass(frozen=True, slots=True)
class LinkResult:
    status: str
    qid: str | None = None
    title: str | None = None  # the article the link finally reached, when it resolved

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED


def _tidy(title: str) -> str:
    return " ".join(title.replace("_", " ").split())


def parse_wikipedia_value(value: str) -> tuple[str, str | None]:
    """(`title`, the article title) when the value names an English article; otherwise
    (`other_language` | `section_link` | `bad_value`, None)."""
    text = value.strip()
    if not text or _ILLEGAL.search(text):
        return "bad_value", None
    url = _URL.fullmatch(text)
    if url:
        if url.group(1).lower() != "en":
            return "other_language", None
        title = unquote(url.group(2))
    elif re.match(r"https?://", text, re.IGNORECASE):
        return "bad_value", None
    else:
        title = text.lstrip(":")
        prefix = _LANGUAGE_PREFIX.fullmatch(title)
        if prefix:
            if prefix.group(1).lower() != "en":
                return "other_language", None
            title = prefix.group(2)
    if "#" in title:
        return "section_link", None
    title = _tidy(title)
    if not title or len(title) > MAX_TITLE_LENGTH:
        return "bad_value", None
    return "title", title


def _canonical(title: str) -> str:
    """MediaWiki treats the first letter as case-insensitive."""
    return title[:1].upper() + title[1:]


def _batches(items: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(items), BATCH_SIZE):
        yield items[start : start + BATCH_SIZE]


def _classify(title: str, norm: dict, redirects: dict, pages: dict) -> LinkResult:
    current = norm.get(title, title)
    via_redirect = False
    for _ in range(_MAX_REDIRECT_HOPS):
        hop = redirects.get(current)
        if hop is None:
            break
        if hop.get("tofragment"):
            return LinkResult("section_link")
        current, via_redirect = hop["to"], True
    page = pages.get(current)
    if page is None:
        return LinkResult("no_answer")
    if page.get("missing") or page.get("invalid"):
        return LinkResult("missing")
    props = page.get("pageprops", {})
    if "disambiguation" in props:
        return LinkResult("disambiguation")
    qid = props.get("wikibase_item")
    if not isinstance(qid, str) or not _QID.fullmatch(qid):
        return LinkResult("no_item")
    return LinkResult("resolved_via_redirect" if via_redirect else "resolved", qid, current)


def resolve_wikipedia_links(client: PoliteClient, values: Iterable[str]) -> dict[str, LinkResult]:
    """One result per distinct input value. Titles are looked up 50 at a time."""
    results: dict[str, LinkResult] = {}
    wanted: dict[str, list[str]] = {}  # canonical title -> the raw values that name it
    for raw in dict.fromkeys(values):
        kind, title = parse_wikipedia_value(raw)
        if title is None:
            results[raw] = LinkResult(kind)
        else:
            wanted.setdefault(_canonical(title), []).append(raw)

    for batch in _batches(sorted(wanted)):
        reply = api_get(
            client,
            ENWIKI_API,
            {
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item|disambiguation",
                "titles": "|".join(batch),
                "redirects": 1,
                "formatversion": 2,
            },
        )
        query = reply.get("query", {})
        norm = {n["from"]: n["to"] for n in query.get("normalized", [])}
        redirects = {r["from"]: r for r in query.get("redirects", [])}
        pages = {p["title"]: p for p in query.get("pages", []) if "title" in p}
        for title in batch:
            outcome = _classify(title, norm, redirects, pages)
            for raw in wanted[title]:
                results[raw] = outcome
    return results
