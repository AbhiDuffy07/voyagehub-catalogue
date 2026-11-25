"""A tiny world for the `nearby` (B18) tests: every input folder with its manifest, and fake
Wikidata and Commons APIs (shapes checked against the real API on 2026-11-23).

Cities: 1 Jaipur (IN) has one photographed place, so it is short; 2 Amer (IN) has three and is
not; 3 Springfield (US, world runs only) has none. Jaipur's listings: Hawa Mahal (a point, no
photo), City Palace (photographed), Old Cafe (an eat listing, only a neighbour) and Jantar Mantar
(no point).
"""

import hashlib
import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx

from catalogue.nearby.rules import NearbyRules, load_nearby_rules
from tests.apifakes import coordinate_claim, entity, item_claim, string_claim
from tests.photofakes import COMMONS, _page, commons_file

WIKIDATA = "https://www.wikidata.org/w/api.php"
TODAY = date(2026, 11, 23)
M = 1 / 111_195  # about one metre of latitude, in degrees
JAIPUR = (26.92, 75.82)
AMER = (26.985, 75.851)


def geo_on() -> NearbyRules:
    """The committed rules with Commons geosearch switched on: the mechanism stays tested while
    the committed rules keep it off (owner ruling 2026-11-25)."""
    return replace(load_nearby_rules(), geosearch_enabled=True)


SPRINGFIELD = (42.10, -72.59)
HAWA = (26.9239, 75.8267)
CITY_PALACE = (26.9258, 75.8237)
FENWAY = (42.1010, -72.5910)
INDIA, USA, MUSEUM, FORT, TEMPLE, HOTEL = "Q668", "Q30", "Q33506", "Q1785071", "Q842402", "Q27686"
PHOTO = {
    "commons_file": "City Palace.jpg",
    "description_url": "https://commons.wikimedia.org/wiki/File:City_Palace.jpg",
    "thumb_url": "https://upload.wikimedia.org/thumb/City_Palace.jpg",
    "thumb_width": 1280,
    "width": 4000,
    "height": 3000,
    "author": "Ann",
    "licence_name": "CC BY-SA 4.0",
    "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "attribution": "Ann, CC BY-SA 4.0, via Wikimedia Commons",
    "sources": ["wikidata_p18"],
}


def north(point, metres):
    return (point[0] + metres * M, point[1])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return sha(path)


def write_manifest(folder: Path, body: dict) -> str:
    path = folder / "manifest.json"
    path.write_text(json.dumps({"dump_date": "20261101", **body}), encoding="utf-8")
    return sha(path)


def listing(pid, ordinal, owner, name, kind="see", point=None, country="IN", **extra):
    return {
        "page_id": pid,
        "ordinal": ordinal,
        "owner_page_id": owner,
        "owner_type": "city",
        "kind": kind,
        "name": name,
        "listing_key": f"{kind}:{name.lower().replace(' ', '-')}",
        "duplicate_of": None,
        "lat": str(point[0]) if point else "",
        "long": str(point[1]) if point else "",
        "quality_flags": [] if point else ["coord_missing"],
        "wikidata": "",
        "country_iso": country,
        **extra,
    }


def selected(pid, ordinal, subject="listing", photo=PHOTO, hero=False):
    return {
        "subject": subject,
        "page_id": pid,
        "ordinal": ordinal,
        "listing_key": None,
        "photo": dict(photo) if photo else None,
        "hero_quality": hero,
    }


def build(tmp_path: Path, *, world=False, listing_facts=()):
    root = tmp_path / ("world" if world else "india")
    country = None if world else "IN"
    articles = [
        {"wikivoyage_page_id": 1, "title": "Jaipur", "type": "city", "tree_country_iso": "IN"},
        {"wikivoyage_page_id": 2, "title": "Amer", "type": "city", "tree_country_iso": "IN"},
        {"wikivoyage_page_id": 9, "title": "Rajasthan", "type": "region", "tree_country_iso": "IN"},
    ]
    listings = [
        listing(1, 1, 1, "Hawa Mahal", point=HAWA),
        listing(1, 2, 1, "City Palace", point=CITY_PALACE),
        listing(1, 3, 1, "Old Cafe", kind="eat", point=north(HAWA, 400)),
        listing(1, 4, 1, "Jantar Mantar"),
        *(listing(2, i, 2, f"Amer sight {i}", point=north(AMER, 100 * i)) for i in (1, 2, 3)),
    ]
    destinations = [
        {
            "wikivoyage_page_id": 1,
            "wikidata_id": "Q1001",
            "coord_lat": JAIPUR[0],
            "coord_long": JAIPUR[1],
            "flags": [],
            "tree_country_iso": "IN",
            "title": "Jaipur",
        },
        {
            "wikivoyage_page_id": 2,
            "wikidata_id": "Q1002",
            "coord_lat": AMER[0],
            "coord_long": AMER[1],
            "flags": [],
            "tree_country_iso": "IN",
            "title": "Amer",
        },
        {
            "wikivoyage_page_id": 9,
            "wikidata_id": "Q1009",
            "coord_lat": 27.0,
            "coord_long": 74.0,
            "flags": [],
            "tree_country_iso": "IN",
            "title": "Rajasthan",
        },
    ]
    selection = [
        selected(1, 0, "article", hero=True),
        selected(1, 2),
        *(selected(2, i) for i in (1, 2, 3)),
    ]
    if world:
        articles.append(
            {
                "wikivoyage_page_id": 3,
                "title": "Springfield",
                "type": "city",
                "tree_country_iso": "US",
            }
        )
        listings.append(listing(3, 1, 3, "Fenway Park", point=FENWAY, country="US"))
        destinations.append(
            {
                "wikivoyage_page_id": 3,
                "wikidata_id": "Q1003",
                "coord_lat": SPRINGFIELD[0],
                "coord_long": SPRINGFIELD[1],
                "flags": [],
                "tree_country_iso": "US",
                "title": "Springfield",
            }
        )

    discover = root / "discover"
    articles_sha = jsonl(discover / "articles.jsonl", articles)
    lis = root / "listings"
    write_manifest(
        lis,
        {
            "country_filter": country,
            "articles_sha256": articles_sha,
            "outputs_sha256": {"listings.jsonl": jsonl(lis / "listings.jsonl", listings)},
        },
    )
    enrich = root / "enrich"
    write_manifest(
        enrich,
        {
            "country_filter": country,
            "outputs_sha256": {
                "destination_facts.jsonl": jsonl(enrich / "destination_facts.jsonl", destinations),
                "listing_facts.jsonl": jsonl(enrich / "listing_facts.jsonl", list(listing_facts)),
            },
        },
    )
    inputs = {n: sha(enrich / n) for n in ("destination_facts.jsonl", "listing_facts.jsonl")}
    photos = root / "photos"
    candidates_manifest = write_manifest(
        photos,
        {
            "country_filter": country,
            "inputs_sha256": inputs,
            "totals": {"coverage": {"by_country": {}}},
            "outputs_sha256": {
                "photo_candidates.jsonl": jsonl(photos / "photo_candidates.jsonl", [])
            },
        },
    )
    sel = root / "selection"
    write_manifest(
        sel,
        {
            "country_filter": country,
            "photo_candidates_manifest_sha256": candidates_manifest,
            "inputs_sha256": inputs,
            "outputs_sha256": {
                "photo_selection.jsonl": jsonl(sel / "photo_selection.jsonl", selection)
            },
        },
    )
    return {
        "discover_folder": discover,
        "listings_folder": lis,
        "enrich_folder": enrich,
        "photos_folder": photos,
        "selection_folder": sel,
        "out_dir": root / "out",
        "cache_dir": root / "cache",
        "dump_date": "20261101",
        "today": TODAY,
        "country_iso": country,
    }


# --- fake APIs ----------------------------------------------------------------------------------


def wd_item(label, cls, point, image=None, country=INDIA, enwiki=None):
    claims = {
        "P31": [item_claim(cls)],
        "P17": [item_claim(country)],
        "P625": [coordinate_claim(*point)],
    }
    if image:
        claims["P18"] = [string_claim(image)]
    return entity(claims, label=label, enwiki=enwiki)


ITEMS = {
    "Q1001": entity(
        {"P17": [item_claim(INDIA)], "P625": [coordinate_claim(*JAIPUR)]}, label="Jaipur"
    ),
    "Q1002": entity({"P17": [item_claim(INDIA)], "P625": [coordinate_claim(*AMER)]}, label="Amer"),
    "Q1003": entity(
        {"P17": [item_claim(USA)], "P625": [coordinate_claim(*SPRINGFIELD)]}, label="Springfield"
    ),
    "Q2001": wd_item(
        "Albert Hall Museum",
        MUSEUM,
        north(JAIPUR, 1000),
        "Albert Hall.jpg",
        enwiki="Albert Hall Museum",
    ),
    "Q2002": wd_item("Hawa Mahal", FORT, north(HAWA, 30), "Hawa Mahal P18.jpg"),
    "Q2003": wd_item("Grand Hotel", HOTEL, north(JAIPUR, 500), "Grand Hotel.jpg"),
    "Q2004": wd_item("Nahargarh Fort", FORT, north(AMER, -500), "Nahargarh.jpg"),
    "Q2005": wd_item("Birla Mandir", TEMPLE, north(JAIPUR, -1500), "Birla Mandir collage.jpg"),
    "Q2006": wd_item("Lost Temple", TEMPLE, north(JAIPUR, 2000)),
    "Q2007": wd_item("Foreign Museum", MUSEUM, north(JAIPUR, 2500), "Foreign.jpg", country=USA),
    "Q2008": wd_item("Galta Ji", TEMPLE, north(JAIPUR, 3000), "Galta Ji.jpg"),
}
WD_NEAR = {
    JAIPUR: ["Q2001", "Q2002", "Q2003", "Q2004", "Q2005", "Q2006", "Q2007", "Q2008", "Q1002"],
    SPRINGFIELD: [],
}
FILES = {
    "File:Albert Hall.jpg": commons_file(sha1="1" * 40),
    "File:Hawa Mahal P18.jpg": commons_file(sha1="2" * 40),
    "File:Birla Mandir collage.jpg": commons_file(sha1="3" * 40),
    "File:Galta Ji.jpg": commons_file(sha1="4" * 40),
    "File:Nahargarh.jpg": commons_file(sha1="5" * 40),
    "File:Hawa Mahal16.jpg": commons_file(sha1="6" * 40),
    "File:Hawa Mahal tiny.jpg": commons_file(width=500, height=300, sha1="7" * 40),
    "File:Fenway Park 2010.jpg": commons_file(sha1="8" * 40),
    **{f"File:Rajasthani thali {i}.jpg": commons_file(sha1=f"{i}" * 40) for i in range(4)},
}
COMMONS_NEAR = {
    HAWA: [
        ("File:Hawa Mahal16.jpg", HAWA),
        ("File:Hawa Mahal tiny.jpg", north(HAWA, 10)),
        *((f"File:Rajasthani thali {i}.jpg", HAWA) for i in range(4)),
    ],
    FENWAY: [("File:Fenway Park 2010.jpg", north(FENWAY, 20))],
}


def _distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _near(table, gscoord):
    lat, lon = (float(x) for x in gscoord.split("|"))
    for point, value in table.items():
        if abs(point[0] - lat) < 1e-6 and abs(point[1] - lon) < 1e-6:
            return point, value
    return (lat, lon), []


def _point_of(qid):
    value = ITEMS[qid]["claims"]["P625"][0]["mainsnak"]["datavalue"]["value"]
    return value["latitude"], value["longitude"]


def fake_wikidata_api(seen=None, items=None, near=None):
    items = ITEMS if items is None else items
    near = WD_NEAR if near is None else near

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if seen is not None:
            seen.append(dict(params))
        if params.get("list") == "geosearch":
            centre, qids = _near(near, params["gscoord"])
            hits = [
                {
                    "pageid": int(q[1:]),
                    "ns": 0,
                    "title": q,
                    "lat": _point_of(q)[0],
                    "lon": _point_of(q)[1],
                    "dist": round(_distance_m(centre, _point_of(q)), 1),
                    "primary": "",
                }
                for q in qids
            ]
            hits = [h for h in hits if h["dist"] <= float(params["gsradius"])]
            return httpx.Response(200, json={"batchcomplete": "", "query": {"geosearch": hits}})
        body = {}
        for qid in params["ids"].split("|"):
            if qid in items:
                body[qid] = {**items[qid], "id": qid}
            else:
                body[qid] = {"id": qid, "missing": ""}
        return httpx.Response(200, json={"entities": body, "success": 1})

    return answer


def fake_commons_api(seen=None, files=None, near=None):
    files = FILES if files is None else files
    near = COMMONS_NEAR if near is None else near

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if seen is not None:
            seen.append(dict(params))
        if params.get("list") == "geosearch":
            assert params["gsnamespace"] == "6"
            centre, found = _near(near, params["gscoord"])
            hits = [
                {
                    "pageid": i + 1,
                    "ns": 6,
                    "title": title,
                    "lat": point[0],
                    "lon": point[1],
                    "dist": round(_distance_m(centre, point), 1),
                    "primary": "",
                }
                for i, (title, point) in enumerate(found)
            ]
            hits = [h for h in hits if h["dist"] <= float(params["gsradius"])]
            return httpx.Response(200, json={"batchcomplete": "", "query": {"geosearch": hits}})
        pages = []
        for title in params["titles"].split("|"):
            if title in files:
                pages.append(_page(title, files[title]))
            else:
                pages.append({"ns": 6, "title": title, "missing": True})
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": pages}})

    return answer


__all__ = ["COMMONS", "WIKIDATA"]
