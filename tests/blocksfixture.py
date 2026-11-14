"""Small builders for B8 tests: stops at known distances, and tiny input folders."""

import hashlib
import json
import math
from pathlib import Path

from catalogue.blocks.model import Stop

BASE_LAT, BASE_LON = 26.9, 75.8
KM = 1 / 111.19  # degrees of latitude per km


def _point(km_north, km_east):
    lat = BASE_LAT + km_north * KM
    lon = BASE_LON + km_east * KM / math.cos(math.radians(BASE_LAT))
    return lat, lon


_counter = {"n": 0}


def stop(name, *, km_north=0.0, km_east=0.0, **kw):
    """A stop `km_north`/`km_east` from a fixed city point."""
    _counter["n"] += 1
    values = {
        "key": f"see:{name.lower().replace(' ', '-')}",
        "page_id": 1,
        "ordinal": _counter["n"],
        "owner_id": 1,
        "owner_slug": "jaipur",
        "owner_title": "Jaipur",
        "section": "see",
        "kind": None,
        "unesco": False,
        "has_photo": False,
        "hours": None,
    }
    values.update(kw)
    lat, lon = _point(km_north, km_east)
    return Stop(name=name, lat=lat, lon=lon, **values)


def hours(*ranges, days=("mon", "tue", "wed", "thu", "fri", "sat", "sun")):
    return {
        "always_open": False,
        "closed_days": [],
        "overnight": False,
        "schedule": [{"days": list(days), "ranges": [list(r) for r in ranges]}],
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path, rows) -> str:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return sha(path)


def listing_row(
    ordinal,
    name,
    *,
    km_north=0.0,
    km_east=0.0,
    kind="see",
    owner=(1, "jaipur", "Jaipur", "city"),
    page_id=None,
    own_coords=True,
    hours_parsed=None,
    duplicate_of=None,
    quality_flags=(),
):
    lat, lon = _point(km_north, km_east)
    return {
        "page_id": page_id if page_id is not None else owner[0],
        "ordinal": ordinal,
        "listing_key": f"{kind}:{name.lower().replace(' ', '-')}",
        "kind": kind,
        "name": name,
        "lat": f"{lat:.6f}" if own_coords else "",
        "long": f"{lon:.6f}" if own_coords else "",
        "owner_page_id": owner[0],
        "owner_slug": owner[1],
        "owner_title": owner[2],
        "owner_type": owner[3],
        "hours_parsed": hours_parsed,
        "hours_status": "parsed" if hours_parsed else "empty",
        "duplicate_of": duplicate_of,
        "quality_flags": list(quality_flags),
        "country_iso": "IN",
    }


def fact_row(row, *, kind=None, flags=(), unesco=False, coord="own"):
    """A listing_facts row for a listing row. coord: 'own' copies its point, None gives no point,
    or a (lat, long) tuple (a Wikidata fill)."""
    if coord == "own":
        lat, lon, source = float(row["lat"]), float(row["long"]), "wikivoyage"
    elif coord is None:
        lat = lon = source = None
    else:
        (lat, lon), source = coord, "wikidata"
    return {
        "page_id": row["page_id"],
        "ordinal": row["ordinal"],
        "listing_key": row["listing_key"],
        "listing_kind": row["kind"],
        "country_iso": "IN",
        "kind": kind,
        "is_unesco": unesco,
        "flags": list(flags),
        "coord_lat": lat,
        "coord_long": lon,
        "coord_source": source,
        "coord_filled": source == "wikidata",
    }


def article_row(page_id, title, kind_type, parent, iso="IN"):
    """A row of discover's articles.jsonl (only what B8 reads)."""
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "type": kind_type,
        "parent": parent,
        "role": "destination_outline",
        "tree_country_iso": iso,
    }


def dest_fact(page_id, title, kind, *, km_north=0.0, km_east=0.0, flags=(), coord=True, qid=None):
    """A row of the enrich output's destination_facts.jsonl (only what B8 reads)."""
    lat, lon = _point(km_north, km_east)
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "kind": kind,
        "is_unesco": False,
        "flags": list(flags),
        "coord_lat": lat if coord else None,
        "coord_long": lon if coord else None,
        "coord_source": "wikidata" if coord else None,
        "wikidata_id": qid,
    }


def write_inputs(
    root: Path,
    listings,
    facts,
    photos=(),
    *,
    country_filter="IN",
    articles=(),
    dest_facts=(),
    photo_articles=(),
    summaries=(),
):
    """Write listings/, enrich/, photo_selection/ (and discover/, summaries/ beside them);
    returns the first three paths. `photos` lists listing rows that have a selected photo,
    `photo_articles` article page ids, `summaries` (subject, page_id, ordinal) tuples."""
    disc = root / "discover"
    disc.mkdir(parents=True)
    ah = _jsonl(disc / "articles.jsonl", articles)
    lis = root / "listings"
    lis.mkdir(parents=True)
    lh = _jsonl(lis / "listings.jsonl", listings)
    (lis / "manifest.json").write_text(
        json.dumps(
            {
                "country_filter": country_filter,
                "articles_sha256": ah,
                "outputs_sha256": {"listings.jsonl": lh},
            }
        ),
        encoding="utf-8",
    )
    enr = root / "enrich"
    enr.mkdir()
    fh = _jsonl(enr / "listing_facts.jsonl", facts)
    dh = _jsonl(enr / "destination_facts.jsonl", dest_facts)
    (enr / "manifest.json").write_text(
        json.dumps(
            {
                "country_filter": country_filter,
                "outputs_sha256": {"listing_facts.jsonl": fh, "destination_facts.jsonl": dh},
            }
        ),
        encoding="utf-8",
    )
    pho = root / "photo_selection"
    pho.mkdir()
    rows = [
        {
            "subject": "listing",
            "page_id": r["page_id"],
            "ordinal": r["ordinal"],
            "photo": {"commons_file": "X.jpg"},
        }
        for r in photos
    ] + [
        {"subject": "article", "page_id": pid, "ordinal": 0, "photo": {"commons_file": "X.jpg"}}
        for pid in photo_articles
    ]
    ph = _jsonl(pho / "photo_selection.jsonl", rows)
    (pho / "manifest.json").write_text(
        json.dumps(
            {"country_filter": country_filter, "outputs_sha256": {"photo_selection.jsonl": ph}}
        ),
        encoding="utf-8",
    )
    summ = root / "summaries"
    summ.mkdir()
    sh = _jsonl(
        summ / "summaries.jsonl",
        [
            {"subject": subject, "page_id": pid, "ordinal": ordinal, "summary": "A summary."}
            for subject, pid, ordinal in summaries
        ],
    )
    (summ / "manifest.json").write_text(
        json.dumps({"country_filter": country_filter, "outputs_sha256": {"summaries.jsonl": sh}}),
        encoding="utf-8",
    )
    return lis, enr, pho
