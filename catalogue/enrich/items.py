"""Fetch Wikidata items in batches and parse them with `claims.parse_item`.

One `wbgetentities` request carries up to 50 IDs, asks only for the parts B4 reads (claims, the
English-Wikipedia sitelink, English labels and descriptions), and goes through `api_get`, so the
`maxlag` rule and the polite client's rate limits apply.

Nothing is dropped silently:
- `missing`: IDs Wikidata has no item for.
- `redirects`: IDs of items that were merged into another one (old -> new). The facts are stored
  under the new ID only; the old ID is left for the caller to flag.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from catalogue.enrich.api import api_get
from catalogue.enrich.claims import ItemFacts, parse_item
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BATCH_SIZE = 50
PROPS = "claims|sitelinks|labels|descriptions"
_QID = re.compile(r"Q[1-9][0-9]*")


@dataclass(frozen=True, slots=True)
class ItemsResult:
    items: dict[str, ItemFacts]
    missing: frozenset[str]
    redirects: dict[str, str]


def _batches(qids: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(qids), BATCH_SIZE):
        yield qids[start : start + BATCH_SIZE]


def fetch_items(client: PoliteClient, qids: Iterable[str]) -> ItemsResult:
    """Facts for every well-formed ID, requested in ascending numeric order."""
    wanted = sorted({q for q in qids if _QID.fullmatch(q)}, key=lambda q: int(q[1:]))
    items: dict[str, ItemFacts] = {}
    missing: set[str] = set()
    redirects: dict[str, str] = {}
    for batch in _batches(wanted):
        reply = api_get(
            client,
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": PROPS,
                "languages": "en",
                "sitefilter": "enwiki",
            },
        )
        entities = reply.get("entities")
        if not isinstance(entities, dict) or not set(batch) <= set(entities):
            # A truncated reply must not turn into quiet `item_missing` flags on the lost items.
            raise PipelineError("Wikidata's reply did not include every requested item")
        for requested, entity in entities.items():
            if "missing" in entity:
                missing.add(requested)
                continue
            actual = entity.get("id", requested)
            if actual != requested:
                redirects[requested] = actual
            if actual not in items:
                items[actual] = parse_item(actual, entity)
    return ItemsResult(items, frozenset(missing), redirects)
