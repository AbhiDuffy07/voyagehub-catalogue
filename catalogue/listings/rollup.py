"""Who owns a listing: the nearest page above it that is not a district.

A district's listings roll up to its city (or region), even when the district is only an outline
article, so a city is not shown empty because its content sits on district pages. A listing on any
other page belongs to that page. If the chain of parents is broken, there is no owner: it is never
guessed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Owner:
    page_id: int
    title: str
    type: str | None
    status: str | None
    via_district: bool


def resolve_owner(
    article: Mapping[str, Any], by_title: Mapping[str, Mapping[str, Any]]
) -> Owner | None:
    if article["type"] != "district":
        return _owner(article, via_district=False)
    seen = {article["title"]}
    for title in article["ancestors"]:  # nearest first
        if title in seen:
            return None  # a cycle
        seen.add(title)
        parent = by_title.get(title)
        if parent is None:
            return None  # the chain is broken
        if parent["type"] != "district":
            return _owner(parent, via_district=True)
    return None


def _owner(article: Mapping[str, Any], *, via_district: bool) -> Owner:
    return Owner(
        page_id=article["wikivoyage_page_id"],
        title=article["title"],
        type=article["type"],
        status=article["status"],
        via_district=via_district,
    )
