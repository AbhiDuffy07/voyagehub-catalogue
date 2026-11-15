"""Build one city's published file from already-verified upstream rows (pure, no I/O).

Nothing is guessed: a missing value stays empty. A photo or summary without a licence or a link is
left out and counted, because every published photo and summary must keep author, licence and link.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from catalogue.blocks.inputs import _point
from catalogue.publish.models import SCHEMA_VERSION

COST_FIELDS = (
    "basis",
    "complete",
    "currency",
    "total_low_minor",
    "total_high_minor",
    "note",
    "transport",
)
BAND_FIELDS = (
    "kind",
    "group",
    "basis",
    "currency",
    "low_minor",
    "high_minor",
    "median_minor",
    "label",
    "n_prices",
    "n_fresh",
)


@dataclass(slots=True)
class Upstream:
    """Rows indexed for lookup. Built once per run by publish.run."""

    place_listings: dict[int, list[dict[str, Any]]]
    photos: dict[tuple[str, int, int], dict[str, Any]]
    summaries: dict[tuple[str, int, int], dict[str, Any]]
    timezones: dict[int, dict[str, Any]]
    blocks: dict[int, list[dict[str, Any]]]
    stops: dict[str, list[dict[str, Any]]]
    block_costs: dict[str, dict[str, Any]]
    days: dict[int, list[dict[str, Any]]]
    bands: dict[int, list[dict[str, Any]]]
    counts: Counter = field(default_factory=Counter)


def photo_of(row: dict[str, Any] | None, counts: Counter) -> dict[str, Any] | None:
    photo = (row or {}).get("photo")
    if not photo:
        return None
    if not (photo.get("licence_name") and photo.get("description_url") and photo.get("thumb_url")):
        counts["photos_left_out_no_credit"] += 1
        return None
    return {
        "thumb_url": photo["thumb_url"],
        "thumb_width": photo.get("thumb_width"),
        "width": photo.get("width"),
        "height": photo.get("height"),
        "credit": {
            "author": photo.get("author") or None,
            "licence": photo["licence_name"],
            "licence_url": photo.get("licence_url") or None,
            "source_url": photo["description_url"],
            "attribution": photo.get("attribution") or photo.get("author") or "",
        },
    }


def text_of(row: dict[str, Any] | None, counts: Counter) -> dict[str, Any] | None:
    if not row or not row.get("summary"):
        return None
    source = row.get("permalink") or row.get("url")
    if not (row.get("licence") and source and row.get("attribution")):
        counts["summaries_left_out_no_credit"] += 1
        return None
    return {
        "text": row["summary"],
        "credit": {
            "author": "Wikipedia contributors",
            "licence": row["licence"],
            "licence_url": row.get("licence_url") or None,
            "source_url": source,
            "attribution": row["attribution"],
            "history_url": row.get("history_url") or None,
        },
    }


def _cost(pair: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pair:
        return None
    return {
        "label": pair["label"],
        **{who: {k: pair[who].get(k) for k in COST_FIELDS} for who in ("foreigner", "resident")},
    }


def _place(listing: dict[str, Any], up: Upstream) -> dict[str, Any] | None:
    key = ("listing", listing["page_id"], listing["ordinal"])
    photo = photo_of(up.photos.get(key), up.counts)
    if photo is None:
        return None  # a place on the page is a photographed place (B12 counts the same)
    point = _point(listing.get("lat"), listing.get("long"))
    return {
        "key": listing["listing_key"],
        "name": listing["name"].strip(),
        "kind": listing["kind"],
        "lat": point[0] if point else None,
        "long": point[1] if point else None,
        "summary": text_of(up.summaries.get(key), up.counts),
        "photo": photo,
    }


def build_city(row: dict[str, Any], up: Upstream) -> dict[str, Any]:
    pid = row["wikivoyage_page_id"]
    zone = up.timezones.get(pid)
    places = [
        p
        for listing in sorted(
            up.place_listings.get(pid, []), key=lambda r: (r["page_id"], r["ordinal"])
        )
        if (p := _place(listing, up)) is not None
    ]
    blocks = [
        {
            "id": b["block_id"],
            "slot": b["slot"],
            "title": b.get("title"),
            "intro": b.get("intro"),
            "minutes": b.get("minutes"),
            "stop_count": b.get("stop_count"),
            "walk_km": b.get("walk_km"),
            "day_trip": b.get("day_trip"),
            "stops": [
                {
                    "key": s.get("listing_key"),
                    "name": s["name"],
                    "lat": s.get("lat"),
                    "long": s.get("long"),
                    "minutes_min": s.get("minutes_min"),
                    "minutes_max": s.get("minutes_max"),
                }
                for s in sorted(up.stops.get(b["block_id"], []), key=lambda s: s["position"])
            ],
            "cost": _cost(up.block_costs.get(b["block_id"])),
        }
        for b in sorted(up.blocks.get(pid, []), key=lambda b: b["block_id"])
    ]
    days = [
        {"id": d["day_id"], "block_ids": d["block_ids"], "cost": _cost(d)}
        for d in sorted(up.days.get(pid, []), key=lambda d: d["day_id"])
    ]
    bands = [
        {k: b.get(k) for k in BAND_FIELDS}
        for b in sorted(up.bands.get(pid, []), key=lambda b: (b["kind"], b["group"], b["basis"]))
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": row["slug"],
        "title": row["title"],
        "country_iso": row.get("country_iso"),
        "tier": row["tier"],
        "summary": text_of(up.summaries.get(("article", pid, 0)), up.counts),
        "hero": photo_of(up.photos.get(("article", pid, 0)), up.counts),
        "timezone": {"name": zone["timezone"], "basis": zone.get("timezone_basis")}
        if zone and zone.get("timezone")
        else None,
        "places": places,
        "blocks": blocks,
        "days": days,
        "price_bands": bands,
    }
