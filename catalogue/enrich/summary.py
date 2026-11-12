"""Short Wikipedia summaries through the MediaWiki Action API (B4b).

One request carries up to 20 titles (the API's limit for extracts) and asks for the plain-text
lead (`extracts`, `exintro`, `explaintext`), the latest revision (`revisions`) and whether the
page is a disambiguation page (`pageprops`). The REST `page/summary` endpoint takes one title per
request and is on the way out with RESTBase, which is why this uses the Action API (owner ruling,
2026-11-12).

Wikipedia text is CC BY-SA 4.0: every row built from it keeps the article link, a permalink to the
revision read, a link to the page history (where the authors are listed), the licence and an
attribution that says it is an excerpt. Nothing is rewritten: the stored text is what the API
returned, and `first_paragraph` only cuts it at a line break.
"""

import re
import tomllib
from collections.abc import Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from catalogue.enrich.api import api_get
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient

ENWIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 20  # the API limit for extracts
MAX_CONTINUATIONS = 20
MAX_REDIRECT_HOPS = 5
DEFAULT_WORKERS = 3  # the polite client's own limit: at most 3 requests at once
LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION_TEXT = "Wikipedia contributors"
# What Wikipedia's extract generator leaves behind when it strips a pronunciation or audio
# template: "Agra (; Hindi: ...)", "Place ()", "Place (, a village)".
_GAP = re.compile(r"\(\s*[;,]|\(\s*\)|[;,]\s*\)")
SUMMARY_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "summary_rules.toml"


@dataclass(frozen=True, slots=True)
class PageSummary:
    status: str  # ok, missing, disambiguation, no_answer
    title: str | None = None  # the article the request finally reached
    extract: str | None = None  # the plain-text lead; None when there is none
    revision_id: int | None = None
    timestamp: str | None = None
    redirected: bool = False


# --- links and text -----------------------------------------------------------------------------


def _slug(title: str) -> str:
    return quote(title.replace(" ", "_"), safe="/(),:")


def article_url(title: str) -> str:
    return f"https://en.wikipedia.org/wiki/{_slug(title)}"


def permalink(title: str, revision_id: int) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={_slug(title)}&oldid={revision_id}"


def history_url(title: str) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={_slug(title)}&action=history"


def attribution(title: str) -> str:
    return f'From the Wikipedia article "{title}" by {ATTRIBUTION_TEXT}, {LICENCE} (excerpt)'


def has_extract_gap(text: str) -> bool:
    """True when the text has an empty or dangling bracket, the trace of removed markup. It is
    only reported: the text is stored exactly as the API returned it."""
    return _GAP.search(text) is not None


def first_paragraph(text: str) -> str:
    """The first non-empty line of the plain-text lead (paragraphs are separated by line breaks)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


# --- fetching -----------------------------------------------------------------------------------


def _batches(titles: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(titles), BATCH_SIZE):
        yield titles[start : start + BATCH_SIZE]


def _read_batch(client: PoliteClient, batch: list[str]) -> dict[str, Any]:
    """Every page, normalisation and redirect of one batch, merged across continuation replies."""
    params: dict[str, str | int] = {
        "action": "query",
        "prop": "extracts|revisions|pageprops",
        "exintro": 1,
        "explaintext": 1,
        "exlimit": BATCH_SIZE,
        "rvprop": "ids|timestamp",
        "ppprop": "disambiguation",
        "redirects": 1,
        "titles": "|".join(batch),
        "formatversion": 2,
    }
    pages: dict[str, dict[str, Any]] = {}
    normalized: dict[str, str] = {}
    redirects: dict[str, str] = {}
    extra: dict[str, str | int] = {}
    for _ in range(MAX_CONTINUATIONS + 1):
        reply = api_get(client, ENWIKI_API, {**params, **extra})
        query = reply.get("query", {})
        for pair in query.get("normalized", []):
            normalized[pair["from"]] = pair["to"]
        for pair in query.get("redirects", []):
            redirects[pair["from"]] = pair["to"]
        for page in query.get("pages", []):
            if "title" in page:
                pages.setdefault(page["title"], {}).update(page)
        more = reply.get("continue")
        if not more:
            return {"pages": pages, "normalized": normalized, "redirects": redirects}
        extra = dict(more)
    raise PipelineError("Wikipedia kept asking to continue a reply; stopping")


def _summary(requested: str, read: dict[str, Any]) -> PageSummary:
    current = read["normalized"].get(requested, requested)
    redirected = False
    for _ in range(MAX_REDIRECT_HOPS):
        target = read["redirects"].get(current)
        if target is None:
            break
        current, redirected = target, True
    page = read["pages"].get(current)
    if page is None:
        return PageSummary("no_answer")
    if page.get("missing") or page.get("invalid"):
        return PageSummary("missing", current, redirected=redirected)
    if "disambiguation" in page.get("pageprops", {}):
        return PageSummary("disambiguation", current, redirected=redirected)
    extract = page.get("extract")
    revisions = page.get("revisions") or [{}]
    revision_id, timestamp = revisions[0].get("revid"), revisions[0].get("timestamp")
    return PageSummary(
        "ok",
        current,
        extract.strip() if isinstance(extract, str) else None,
        revision_id if isinstance(revision_id, int) else None,
        timestamp if isinstance(timestamp, str) else None,
        redirected,
    )


def fetch_summaries(
    client: PoliteClient, titles: Iterable[str], *, workers: int = DEFAULT_WORKERS
) -> dict[str, PageSummary]:
    """One result per distinct requested title, in batches of 20 in sorted order.

    Up to `workers` batches are in flight at once. The polite client still enforces its own limits
    (3 at once, 200 a minute, Retry-After), so more workers than 3 would only queue. The result
    does not depend on the order in which the batches finish. When a batch fails, nothing further
    is started and the error is raised once the batches already running have ended.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    pending = iter(list(_batches(sorted(set(titles)))))
    results: dict[str, PageSummary] = {}
    in_flight: dict[Future[dict[str, Any]], list[str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def start_next() -> None:
            batch = next(pending, None)
            if batch is not None:
                in_flight[pool.submit(_read_batch, client, batch)] = batch

        for _ in range(workers):
            start_next()
        try:
            while in_flight:
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in finished:
                    batch = in_flight.pop(future)
                    read = future.result()
                    for requested in batch:
                        results[requested] = _summary(requested, read)
                    start_next()
        except BaseException:
            for future in in_flight:
                future.cancel()
            raise
    return results


# --- the rules file -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SummaryRules:
    version: int
    very_short_chars: int
    reasons: dict[str, str] = field(default_factory=dict)


def load_summary_rules(path: Path = SUMMARY_RULES_PATH) -> SummaryRules:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PipelineError(f"{path.name} needs an integer rules_version")
    summary = document.get("summary")
    rule = summary.get("very_short") if isinstance(summary, dict) else None
    chars = rule.get("chars") if isinstance(rule, dict) else None
    reason = rule.get("reason") if isinstance(rule, dict) else None
    if isinstance(chars, bool) or not isinstance(chars, int) or chars <= 0:
        raise PipelineError(f"{path.name}: summary.very_short needs a positive whole number chars")
    if not isinstance(reason, str) or not reason.strip():
        raise PipelineError(f"{path.name}: summary.very_short needs a written reason")
    return SummaryRules(version, chars, {"very_short_chars": reason.strip()})
