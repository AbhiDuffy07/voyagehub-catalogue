"""The media statements of Wikidata items (B5): image (P18) and Commons category (P373).

`enrich-wikidata` keeps one image name per item and drops it when an item has several, so a file
that stored `image: None` cannot say "none" from "several". This is the small batched re-read that
tells them apart, and it also brings the Commons category (P373), which B4 does not read. It uses
the same `wbgetentities` call shape, the same `api_get` etiquette and the same rank rule as B4
(`claims._usable`: deprecated statements are ignored; when any statement is preferred, only those
count). Nothing is chosen between several images: every distinct file is returned with its rank.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from catalogue.enrich.api import api_get
from catalogue.enrich.claims import _datavalue, _usable
from catalogue.enrich.items import BATCH_SIZE, WIKIDATA_API
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.progress import Progress

IMAGE = "P18"
COMMONS_CATEGORY = "P373"
_QID = re.compile(r"Q[1-9][0-9]*")


@dataclass(frozen=True, slots=True)
class MediaClaims:
    p18: tuple[tuple[str, str], ...] = ()  # (file name, statement rank), in statement order
    commons_category: str | None = None  # only when the item names exactly one


def _parse(entity: dict) -> MediaClaims:
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return MediaClaims()
    files: dict[str, str] = {}
    for statement in _usable(claims.get(IMAGE, [])):
        value = _datavalue(statement)
        if isinstance(value, str) and value and value not in files:
            rank = statement.get("rank")
            files[value] = rank if rank in ("preferred", "normal") else "normal"
    categories = {
        v
        for v in (_datavalue(s) for s in _usable(claims.get(COMMONS_CATEGORY, [])))
        if isinstance(v, str) and v
    }
    only = next(iter(categories)) if len(categories) == 1 else None
    return MediaClaims(tuple(files.items()), only)


def fetch_media_claims(client: PoliteClient, qids: Iterable[str]) -> dict[str, MediaClaims]:
    """Media statements for every well-formed ID, asked in ascending numeric order, 50 a request.

    Items Wikidata has no entry for are left out of the result. A merged item is returned under the
    ID that was asked for. A reply that leaves out a requested ID stops the run.
    """
    wanted = sorted({q for q in qids if _QID.fullmatch(q)}, key=lambda q: int(q[1:]))
    result: dict[str, MediaClaims] = {}
    progress = Progress("Wikidata media items", len(wanted))
    for start in range(0, len(wanted), BATCH_SIZE):
        batch = wanted[start : start + BATCH_SIZE]
        reply = api_get(
            client,
            WIKIDATA_API,
            {"action": "wbgetentities", "ids": "|".join(batch), "props": "claims"},
        )
        entities = reply.get("entities")
        if not isinstance(entities, dict) or not set(batch) <= set(entities):
            raise PipelineError("Wikidata's reply did not include every requested item")
        for requested in batch:
            entity = entities[requested]
            if isinstance(entity, dict) and "missing" not in entity:
                result[requested] = _parse(entity)
        progress.advance(len(batch))
    progress.done()
    return result
