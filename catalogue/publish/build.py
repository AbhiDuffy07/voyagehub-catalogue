"""Build one city's published file from already-verified upstream rows (pure, no I/O).

Nothing is guessed: a missing value stays empty. A photo or summary without a licence or a link is
left out and counted, because every published photo and summary must keep author, licence and link.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from catalogue.blocks.inputs import _point
from catalogue.publish.getting_around import (
    ContentRules,
    apps_for,
    excerpt_of,
    load_content_rules,
    load_ride_apps,
)
from catalogue.publish.hero import HeroRules, author_problem, hero_problem, load_hero_rules
from catalogue.publish.models import SCHEMA_VERSION
from catalogue.publish.summary_text import (
    SummaryRules,
    adapted_attribution,
    display_summary,
    load_summary_rules,
)

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
    "n_free",
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
    stop_listings: dict[str, dict[str, Any]] = field(default_factory=dict)  # by listing_key
    sections: dict[int, dict[str, Any]] = field(default_factory=dict)  # "Get around", by page id
    ride_apps: dict[str, dict[str, Any]] = field(default_factory=load_ride_apps)
    hero_rules: HeroRules = field(default_factory=load_hero_rules)
    content_rules: ContentRules = field(default_factory=load_content_rules)
    summary_rules: SummaryRules = field(default_factory=load_summary_rules)


def _https(*urls: Any) -> bool:
    return all(isinstance(u, str) and u.startswith("https://") for u in urls)


def _web(url: Any) -> bool:
    """A licence link may still be an old http:// Creative Commons address; nothing loads it."""
    return isinstance(url, str) and url.startswith(("https://", "http://"))


def photo_of(
    row: dict[str, Any] | None, counts: Counter, rules: HeroRules | None = None
) -> dict[str, Any] | None:
    """The published form of a photo row, or None. With `rules`, a photo whose author fails the
    author rule is left out too and counted (place photos; a hero is checked by hero_problem)."""
    photo = (row or {}).get("photo")
    if not photo:
        return None
    if not (
        photo.get("licence_name")
        and (photo.get("author") or photo.get("attribution"))
        and _https(photo.get("description_url"), photo.get("thumb_url"))
        and (not photo.get("licence_url") or _web(photo["licence_url"]))
    ):
        counts["photos_left_out_no_credit"] += 1
        return None
    if rules is not None and author_problem(photo, rules):
        counts["photos_left_out_bad_author"] += 1
        return None
    thumb_width, width = photo.get("thumb_width"), photo.get("width")
    if isinstance(thumb_width, int) and isinstance(width, int) and 0 < width < thumb_width:
        thumb_width = width  # a small original is served as it is, not enlarged
    return {
        "thumb_url": photo["thumb_url"],
        "thumb_width": thumb_width,
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


def text_of(
    row: dict[str, Any] | None, counts: Counter, rules: SummaryRules
) -> dict[str, Any] | None:
    """The published summary: the display text (brackets out, two or three sentences) with the
    stored credit, marked "adapted" when text was taken out. The stored row is not changed."""
    if not row or not row.get("summary"):
        return None
    source = row.get("permalink") or row.get("url")
    if not (row.get("licence") and row.get("attribution") and _https(source)):
        counts["summaries_left_out_no_credit"] += 1
        return None
    shown = display_summary(row["summary"], rules)
    return {
        "text": shown,
        "credit": {
            "author": "Wikipedia contributors",
            "licence": row["licence"],
            "licence_url": row.get("licence_url") or None,
            "source_url": source,
            "attribution": adapted_attribution(row["attribution"], row["summary"], shown),
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


def _place(listing: dict[str, Any], up: Upstream, is_stop: bool) -> dict[str, Any] | None:
    key = ("listing", listing["page_id"], listing["ordinal"])
    photo = photo_of(up.photos.get(key), up.counts, up.hero_rules)
    if photo is None and not is_stop:
        return None  # a photographed place, as readiness counts it, or a block stop
    point = _point(listing.get("lat"), listing.get("long"))
    return {
        "key": listing["listing_key"],
        "name": listing["name"].strip(),
        "kind": listing.get("kind"),
        "lat": point[0] if point else None,
        "long": point[1] if point else None,
        "summary": text_of(up.summaries.get(key), up.counts, up.summary_rules),
        "photo": photo,
    }


def fallback_hero(pid: int, up: Upstream) -> dict[str, Any] | None:
    """A city with no hero photo of its own shows its best hero-quality place photo, captioned
    with the place name and keeping that photo's credit. Nothing qualifies: stays empty."""
    best: tuple[float, str, dict[str, Any], str] | None = None
    for listing in up.place_listings.get(pid, []):
        row = up.photos.get(("listing", listing["page_id"], listing["ordinal"]))
        if not row or not row.get("hero_quality"):
            continue
        photo = photo_of(row, Counter())  # a throwaway counter: this is a search, not a use
        if photo is None or hero_problem(row, up.hero_rules):
            continue
        score = float(row.get("score") or 0)
        key = listing["listing_key"]
        if best is None or (-score, key) < (-best[0], best[1]):
            best = (score, key, photo, listing["name"].strip())
    return None if best is None else {**best[2], "caption": best[3]}


def _hero_of(pid: int, up: Upstream) -> dict[str, Any] | None:
    """The city's own photo if it passes the hero checks, else the best place photo that does."""
    row = up.photos.get(("article", pid, 0))
    if row and row.get("photo"):
        problem = hero_problem(row, up.hero_rules)
        if problem:
            up.counts[f"own_hero_rejected_{problem}"] += 1
        elif (own := photo_of(row, up.counts)) is not None:
            return {**own, "caption": None}
    return fallback_hero(pid, up)


def _stop_listing(stop: dict[str, Any], up: Upstream) -> dict[str, Any]:
    """The stop's listing; when the listings file lacks it, what the stop itself says."""
    found = up.stop_listings.get(stop["listing_key"])
    if found is not None and (found.get("name") or "").strip():
        return found
    return {
        "listing_key": stop["listing_key"],
        "page_id": stop["page_id"],
        "ordinal": stop["ordinal"],
        "name": stop["name"],
        "kind": (found or {}).get("kind"),
        "lat": stop.get("lat"),
        "long": stop.get("long"),
    }


def _getting_around(row: dict[str, Any], pid: int, up: Upstream) -> dict[str, Any]:
    section = up.sections.get(pid)
    excerpt = None
    text = excerpt_of(section.get("text") or "", up.content_rules) if section else None
    heading = (section or {}).get("section_heading") or "Get around"
    title = (section or {}).get("page_title") or row["title"]
    source = section.get("source_url") if section else None
    if text and section.get("licence") and section.get("attribution") and _https(source):
        base, _, rest = source.partition("&oldid=")
        excerpt = {
            "text": text,
            "credit": {
                "author": "Wikivoyage contributors",
                "licence": section["licence"],
                "licence_url": section.get("licence_url") or None,
                "source_url": f"{source}#{heading.replace(' ', '_')}",
                "attribution": (
                    f'From the Wikivoyage article "{title}" (section "{heading}") '
                    f"by {section['attribution']}, "
                    f"{section['licence']}"
                ),
                "history_url": f"{base}&action=history" if rest else None,
            },
        }
    elif text:
        up.counts["getting_around_left_out_no_credit"] += 1
    apps = apps_for(up.ride_apps, row.get("country_iso"), row["slug"])
    return {"excerpt": excerpt, "ride_apps": apps}


def build_city(row: dict[str, Any], up: Upstream) -> dict[str, Any]:
    pid = row["wikivoyage_page_id"]
    zone = up.timezones.get(pid)
    stop_rows = [
        s
        for b in up.blocks.get(pid, [])
        for s in up.stops.get(b["block_id"], [])
        if s.get("listing_key")
    ]
    stops_by_key = {s["listing_key"]: s for s in stop_rows}
    entries = {r["listing_key"]: r for r in up.place_listings.get(pid, [])}
    for key, stop in stops_by_key.items():
        if key not in entries:
            entries[key] = _stop_listing(stop, up)
    places = [
        p
        for listing in sorted(entries.values(), key=lambda r: (r["page_id"], r["ordinal"]))
        if (p := _place(listing, up, listing["listing_key"] in stops_by_key)) is not None
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
    hero = _hero_of(pid, up)
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": row["slug"],
        "title": row["title"],
        "country_iso": row.get("country_iso"),
        "tier": row["tier"],
        "summary": text_of(up.summaries.get(("article", pid, 0)), up.counts, up.summary_rules),
        "hero": hero,
        "timezone": {"name": zone["timezone"], "basis": zone.get("timezone_basis")}
        if zone and zone.get("timezone")
        else None,
        "getting_around": _getting_around(row, pid, up),
        "places": places,
        "blocks": blocks,
        "days": days,
        "price_bands": bands,
    }
