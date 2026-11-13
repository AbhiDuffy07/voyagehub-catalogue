"""Wikimedia Commons through the MediaWiki Action API (B5): file facts and quality-marked files.

Two kinds of request, both through the polite client and `api_get` (so `maxlag`, `Retry-After`, the
3-at-once and 200-a-minute limits apply):

- `fetch_file_info`: up to 50 file titles per request (`imageinfo` with `extmetadata`, and
  `categories` limited to the quality marks), continuation merged. One result per distinct title.
- `search_marked_files`: one search per Commons category, for files in it (or its subcategories)
  that carry a quality mark. Capped per category by the rules; the cap is reported, not hidden.

Nothing is guessed: a value Commons does not give stays `None`. The author is stored as plain text
(markup removed); the licence is stored as the code, name and link Commons reports.
"""

import html
import re
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote

from catalogue.enrich.api import api_get
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.photos.rules import PhotoRules
from catalogue.progress import Progress

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
BATCH_SIZE = 50  # titles per request (the API limit without special rights)
SEARCH_LIMIT = 50
MAX_CONTINUATIONS = 20
MAX_REDIRECT_HOPS = 5
DEFAULT_WORKERS = 3  # the polite client's own limit
SEARCH_WORKERS = 1  # Commons search is the expensive backend: one search at a time
BUSY_CODE = "cirrussearch-too-busy-error"
BUSY_RETRIES = 5
BUSY_FIRST_WAIT = 5.0  # doubled each time: 5, 10, 20, 40, 80 seconds
BUSY_GIVE_UP = 3  # this many categories in a row busy: stop asking, flag the rest
# A GET URL much over 8 KB is refused by the servers in front of the API. Titles are sent
# percent-encoded (a Devanagari or CJK letter is 9 characters), so a batch is also limited
# by the encoded length of its titles, which leaves room for the other parameters.
MAX_URL_CHARS = 8000
MAX_TITLES_CHARS = 6000
_TAG = re.compile(r"<[^>]*>")
_HIDDEN = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_SPACE = re.compile(r"\s+")
_UNSAFE_TITLE = re.compile(r"[|\x00-\x1f\x7f]")
_UNSAFE_CATEGORY = re.compile(r'["|\\\x00-\x1f\x7f]')

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class FileInfo:
    status: str  # ok or missing
    title: str  # the file page the request finally reached
    url: str | None = None
    description_url: str | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    sha1: str | None = None
    thumb_url: str | None = None
    thumb_width: int | None = None  # what Commons returned: never wider than the file
    author: str | None = None
    licence_code: str | None = None
    licence_name: str | None = None
    licence_url: str | None = None
    marks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryFiles:
    files: tuple[str, ...] = ()  # at most the cap, sorted by title
    capped: bool = False  # the search found more than the cap
    unusable: bool = False  # the category name cannot be searched safely
    busy: bool = False  # Commons search stayed busy: not searched, so not empty


def strip_html(value: object) -> str | None:
    """Plain text of an HTML fragment (the author field), or None when nothing visible is left."""
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAG.sub(" ", _HIDDEN.sub(" ", value)))
    text = _SPACE.sub(" ", text).strip()
    return text or None


def _run_bounded(
    items: list[T], work: Callable[[T], R], label: str, *, workers: int = DEFAULT_WORKERS
) -> Iterator[tuple[T, R]]:
    """Run `work` over `items` with up to `workers` in flight; yield results as they finish.

    When one fails nothing further is started and the error is raised once those already running
    have ended. Callers must not depend on the order of the results.
    """
    pending = iter(items)
    in_flight: dict[Future[R], T] = {}
    progress = Progress(label, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def start_next() -> None:
            item = next(pending, None)
            if item is not None:
                in_flight[pool.submit(work, item)] = item

        for _ in range(workers):
            start_next()
        try:
            while in_flight:
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in finished:
                    item = in_flight.pop(future)
                    result = future.result()
                    progress.advance()
                    start_next()
                    yield item, result
        except BaseException:
            for future in in_flight:
                future.cancel()
            raise
    progress.done()


class SearchBusyError(PipelineError):
    """Commons search answered "too busy" on every retry."""


def _batches(titles: list[str]) -> Iterator[list[str]]:
    """Batches of at most BATCH_SIZE titles whose percent-encoded, pipe-joined length stays under
    MAX_TITLES_CHARS (a title longer than that on its own still goes alone)."""
    batch: list[str] = []
    size = 0
    for title in titles:
        cost = len(quote(title, safe="")) + 3  # the encoded "|" between titles
        if batch and (len(batch) >= BATCH_SIZE or size + cost > MAX_TITLES_CHARS):
            yield batch
            batch, size = [], 0
        batch.append(title)
        size += cost
    if batch:
        yield batch


def _meta(extmetadata: dict[str, Any], key: str) -> str | None:
    entry = extmetadata.get(key)
    value = entry.get("value") if isinstance(entry, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _read_batch(client: PoliteClient, batch: list[str], rules: PhotoRules) -> dict[str, Any]:
    """Pages, normalisations and redirects of one batch, merged across continuation replies."""
    params: dict[str, str | int] = {
        "action": "query",
        "prop": "imageinfo|categories",
        "iiprop": "url|size|mime|sha1|extmetadata",
        "iiurlwidth": rules.thumb_width,
        "iiextmetadatafilter": "Artist|License|LicenseShortName|LicenseUrl",
        "cllimit": "max",
        "clcategories": "|".join(f"Category:{c}" for c in rules.quality_categories),
        "redirects": 1,
        "titles": "|".join(batch),
        "formatversion": 2,
    }
    pages: dict[str, dict[str, Any]] = {}
    normalized: dict[str, str] = {}
    redirects: dict[str, str] = {}
    extra: dict[str, str | int] = {}
    for _ in range(MAX_CONTINUATIONS + 1):
        reply = api_get(client, COMMONS_API, {**params, **extra})
        try:
            query = reply.get("query", {})
            listed = query.get("pages", [])
            if not isinstance(listed, list):
                raise TypeError("pages is not a list")
            for pair in query.get("normalized", []):
                normalized[pair["from"]] = pair["to"]
            for pair in query.get("redirects", []):
                redirects[pair["from"]] = pair["to"]
            for page in listed:
                if "title" not in page:
                    continue
                entry = pages.setdefault(page["title"], {})
                for key, value in page.items():
                    if key == "categories":
                        entry.setdefault("categories", []).extend(value)
                    else:
                        entry[key] = value
        except (KeyError, TypeError, AttributeError):
            raise PipelineError("Commons answered with an unexpected reply shape") from None
        more = reply.get("continue")
        if not more:
            return {"pages": pages, "normalized": normalized, "redirects": redirects}
        extra = dict(more)
    raise PipelineError("Commons kept asking to continue a reply; stopping")


def _file_info(requested: str, read: dict[str, Any], rules: PhotoRules) -> FileInfo:
    current = read["normalized"].get(requested, requested)
    for _ in range(MAX_REDIRECT_HOPS):
        target = read["redirects"].get(current)
        if target is None:
            break
        current = target
    page = read["pages"].get(current)
    infos = page.get("imageinfo") if isinstance(page, dict) else None
    if not isinstance(page, dict) or page.get("missing") or page.get("invalid"):
        return FileInfo("missing", current)
    if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
        return FileInfo("missing", current)
    info = infos[0]
    extmetadata = info.get("extmetadata")
    meta = extmetadata if isinstance(extmetadata, dict) else {}
    found = {
        c["title"].removeprefix("Category:")
        for c in page.get("categories", [])
        if isinstance(c, dict) and isinstance(c.get("title"), str)
    }
    return FileInfo(
        status="ok",
        title=current,
        url=_text(info.get("url")),
        description_url=_text(info.get("descriptionurl")),
        width=_whole(info.get("width")),
        height=_whole(info.get("height")),
        mime=_text(info.get("mime")),
        sha1=_text(info.get("sha1")),
        thumb_url=_text(info.get("thumburl")),
        thumb_width=_whole(info.get("thumbwidth")) if info.get("thumburl") else None,
        author=strip_html(_meta(meta, "Artist")),
        licence_code=_meta(meta, "License"),
        licence_name=_meta(meta, "LicenseShortName"),
        licence_url=_meta(meta, "LicenseUrl"),
        marks=tuple(c for c in rules.quality_categories if c in found),
    )


def fetch_file_info(
    client: PoliteClient, titles: Iterable[str], rules: PhotoRules
) -> dict[str, FileInfo]:
    """One result per distinct requested `File:` title, in batches of 50 in sorted order."""
    distinct = set(titles)
    # A title with a pipe or a control character would split the request: never asked for.
    results: dict[str, FileInfo] = {
        t: FileInfo("missing", t) for t in distinct if _UNSAFE_TITLE.search(t) or not t.strip()
    }
    ordered = sorted(t for t in distinct if t not in results)
    batches = list(_batches(ordered))
    for batch, read in _run_bounded(
        batches, lambda b: _read_batch(client, b, rules), "Commons files"
    ):
        for requested in batch:
            results[requested] = _file_info(requested, read, rules)
    return results


def _search_reply(
    client: PoliteClient, params: dict[str, str | int], sleep: Callable[[float], None]
) -> dict[str, Any]:
    """One search, waiting out Commons' "search is too busy" answer; after the retries it raises
    SearchBusyError for the caller to flag."""
    for attempt in range(BUSY_RETRIES + 1):
        try:
            return api_get(client, COMMONS_API, params)
        except PipelineError as exc:
            if BUSY_CODE not in str(exc):
                raise
            if attempt == BUSY_RETRIES:
                raise SearchBusyError("Commons search stayed too busy") from None
            sleep(BUSY_FIRST_WAIT * 2**attempt)
    raise AssertionError("unreachable: the loop always returns or raises")  # pragma: no cover


def _search_one(
    client: PoliteClient, category: str, rules: PhotoRules, sleep: Callable[[float], None]
) -> CategoryFiles:
    marks = "|".join(rules.quality_categories)
    reply = _search_reply(
        client,
        {
            "action": "query",
            "list": "search",
            "srsearch": f'deepcat:"{category}" incategory:"{marks}"',
            "srnamespace": 6,
            "srlimit": SEARCH_LIMIT,
            "srprop": "",
            "formatversion": 2,
        },
        sleep,
    )
    try:
        query = reply["query"]
        hits = [h["title"] for h in query["search"]]
        total = query["searchinfo"]["totalhits"]
    except (KeyError, TypeError):
        raise PipelineError("Commons answered a search with an unexpected reply shape") from None
    if not all(isinstance(t, str) for t in hits) or not isinstance(total, int):
        raise PipelineError("Commons answered a search with an unexpected reply shape")
    ordered = sorted(set(hits))
    capped = total > rules.category_cap or len(ordered) > rules.category_cap
    return CategoryFiles(tuple(ordered[: rules.category_cap]), capped)


def search_marked_files(
    client: PoliteClient,
    categories: Iterable[str],
    rules: PhotoRules,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, CategoryFiles]:
    """Quality-marked files of each distinct category name (subcategories included), at most the
    rules' cap each, chosen by title order among the first search results."""
    names = sorted(set(categories))
    results: dict[str, CategoryFiles] = {
        name: CategoryFiles(unusable=True) for name in names if _UNSAFE_CATEGORY.search(name)
    }
    todo = [name for name in names if name not in results]
    # Categories in a row that stayed busy. Safe only because searches run one at a time
    # (SEARCH_WORKERS = 1): with more workers the give-up point would depend on thread timing.
    streak = 0

    def work(name: str) -> CategoryFiles:
        nonlocal streak
        if streak >= BUSY_GIVE_UP:
            return CategoryFiles(busy=True)  # Commons search is down: stop asking
        try:
            found = _search_one(client, name, rules, sleep)
        except SearchBusyError:
            streak += 1
            return CategoryFiles(busy=True)
        streak = 0
        return found

    for name, found in _run_bounded(todo, work, "Commons categories", workers=SEARCH_WORKERS):
        results[name] = found
    return results
