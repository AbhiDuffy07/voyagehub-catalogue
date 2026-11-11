"""Stable listing keys: the future database upsert key of a listing (`wikivoyage_listing_key`).

A key is `<kind>:<key_slug(name)>`. `key_slug` keeps the letters, digits and combining marks of
every script (NFKC, case-folded), so a name in Hindi, Chinese or Danish keeps its own key instead
of collapsing to `unnamed`. A name longer than `MAX_SLUG_LENGTH` characters is cut and ends in a
hash of the whole name. `KEY_VERSION` 2 is this scheme; version 1 (ASCII only, B3a) was never
loaded.

Wikivoyage listings have no ID, so a name used more than once under one owner (a city plus its
districts) is told apart by what the listing itself says, never by the order the dump happens to
be read in:

1. its Wikidata ID, else
2. a short hash of its coordinates rounded to 4 decimals (about 11 m), else
3. a short hash of its normalised address,
4. and only when that still leaves identical listings, their position, counted in page-ID then
   in-page order.

A name used once keeps the plain key. A listing that has no label of its own and shares its name
with labelled ones keeps the plain key too, because the labelled ones never use it.

Known cost: a key changes when a same-named sibling appears under the same owner, and a renamed or
re-edited listing gets a new key. There is no ID in the source to avoid that.
"""

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from typing import NamedTuple

_WIKIDATA = re.compile(r"Q[0-9]+")
KEY_VERSION = 2
MAX_SLUG_LENGTH = 80
_HASH_LENGTH = 8


class KeyInput(NamedTuple):
    scope: int  # the owner's page ID, or the page's own ID when there is no owner
    base: str  # kind:slug
    page_id: int
    ordinal: int
    wikidata: str
    lat: str
    long: str
    address: str


def _short_hash(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324 (a label, not security)
    return digest[:_HASH_LENGTH]


def words(text: str) -> list[str]:
    """NFKC, case-folded words made of letters, digits and combining marks (any script)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in folded)
    return kept.split()


def key_slug(name: str) -> str:
    """The name part of a listing key. Empty or unreadable names give `unnamed`."""
    slug = "-".join(words(name))
    if not slug:
        return "unnamed"
    if len(slug) > MAX_SLUG_LENGTH:
        head = slug[: MAX_SLUG_LENGTH - _HASH_LENGTH - 1].rstrip("-")
        slug = f"{head}-{_short_hash(slug)}"
    return slug


def _coordinate_label(lat: str, long: str) -> str:
    try:
        latitude, longitude = float(lat), float(long)
    except ValueError:
        return ""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude, longitude) == (0, 0):
        return ""
    return "c" + _short_hash(f"{latitude:.4f},{longitude:.4f}")


def address_label(address: str) -> str:
    normal = " ".join(words(address))
    return "a" + _short_hash(normal) if normal else ""


def _label(entry: KeyInput) -> str:
    wikidata = entry.wikidata.strip().upper()
    if _WIKIDATA.fullmatch(wikidata):
        return wikidata
    return _coordinate_label(entry.lat, entry.long) or address_label(entry.address)


def assign_keys(entries: Iterable[KeyInput]) -> dict[tuple[int, int], str]:
    """Return {(page_id, ordinal): listing_key}. The result does not depend on the input order."""
    groups: dict[tuple[int, str], list[KeyInput]] = defaultdict(list)
    for entry in entries:
        groups[(entry.scope, entry.base)].append(entry)

    keys: dict[tuple[int, int], str] = {}
    for (_, base), members in groups.items():
        if len(members) == 1:
            keys[(members[0].page_id, members[0].ordinal)] = base
            continue
        by_label: dict[str, list[KeyInput]] = defaultdict(list)
        for member in members:
            by_label[_label(member)].append(member)
        for label, same in by_label.items():
            same.sort(key=lambda m: (m.page_id, m.ordinal))
            stem = f"{base}~{label}" if label else base
            for position, member in enumerate(same, start=1):
                if len(same) == 1:
                    key = stem
                elif label:
                    key = f"{stem}~{position}"
                else:
                    key = f"{base}~{position}"
                keys[(member.page_id, member.ordinal)] = key
    return keys
