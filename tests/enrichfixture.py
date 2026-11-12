"""Write a tiny discover + fetch-listings output pair for the enrich tests."""

import hashlib
import json
from pathlib import Path


def _jsonl(path: Path, rows) -> str:
    data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    path.write_text(data, encoding="utf-8", newline="\n")
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def article(
    page_id,
    title,
    *,
    type_="city",
    role="destination",
    qid=None,
    iso="IN",
    status="usable",
    country_name=None,
):
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "type": type_,
        "role": role,
        "status": status,
        "wikidata_id": qid,
        "tree_country": country_name or ("India" if iso == "IN" else None),
        "tree_country_iso": iso,
    }


def listing(
    page_id,
    ordinal,
    name,
    *,
    kind="see",
    wikidata="",
    wikipedia="",
    lat="",
    long="",
    page_type="city",
    page_lat="26.92",
    page_long="75.82",
    iso="IN",
    from_marker=False,
):
    return {
        "page_id": page_id,
        "ordinal": ordinal,
        "listing_key": f"{kind}:{name.lower().replace(' ', '-')}",
        "kind": kind,
        "name": name,
        "wikidata": wikidata,
        "wikipedia": wikipedia,
        "lat": lat,
        "long": long,
        "page_type": page_type,
        "page_lat": page_lat,
        "page_long": page_long,
        "country_iso": iso,
        "from_marker": from_marker,
    }


def write_inputs(
    root: Path,
    *,
    articles,
    listings,
    destinations=(),
    country_filter="IN",
    page_geo=None,
):
    """Return (discover_folder, listings_folder). `page_geo` maps page_id -> (lat, long) strings
    for the sections file; by default every article gets Jaipur's point."""
    discover = root / "discover"
    listings_dir = root / "listings"
    discover.mkdir(parents=True)
    listings_dir.mkdir(parents=True)
    articles_hash = _jsonl(discover / "articles.jsonl", articles)
    _jsonl(discover / "destinations.jsonl", destinations)
    (discover / "manifest.json").write_text(json.dumps({"dump_sha1": "x"}), encoding="utf-8")

    geo = page_geo if page_geo is not None else {}
    sections = []
    for row in articles:
        lat, long = geo.get(row["wikivoyage_page_id"], ("26.92", "75.82"))
        sections.append(
            {
                "page_id": row["wikivoyage_page_id"],
                "ordinal": 0,
                "section_key": "lead",
                "page_lat": lat,
                "page_long": long,
            }
        )
    sections_hash = _jsonl(listings_dir / "sections.jsonl", sections)
    listings_hash = _jsonl(listings_dir / "listings.jsonl", listings)
    (listings_dir / "manifest.json").write_text(
        json.dumps(
            {
                "articles_sha256": articles_hash,
                "country_filter": country_filter,
                "dump_date": "20261101",
                "outputs_sha256": {
                    "listings.jsonl": listings_hash,
                    "sections.jsonl": sections_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    return discover, listings_dir
