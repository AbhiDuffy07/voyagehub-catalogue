"""A tiny B5 output folder (and the enrich folder it was built from) for photo-select tests."""

import hashlib
import json
from pathlib import Path

from tests.enrichfixture import destination_row, listing_fact, write_enrich_output


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_row(
    page_id,
    name,
    *,
    ordinal=0,
    subject="article",
    country="IN",
    sources=("wikidata_p18",),
    rank=None,
    marks=(),
    width=4000,
    height=3000,
    mime="image/jpeg",
    flags=(),
    usable=True,
    listing_key=None,
):
    return {
        "page_id": page_id,
        "ordinal": ordinal,
        "subject": subject,
        "listing_key": listing_key,
        "country_iso": country,
        "wikidata_id": f"Q{page_id}",
        "commons_file": name,
        "requested_file": name,
        "sources": sorted(sources),
        "p18_rank": rank,
        "description_url": f"https://commons.wikimedia.org/wiki/File:{name}",
        "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{name}",
        "thumb_url": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{name}/1280px-{name}",
        "thumb_width": min(1280, width),
        "width": width,
        "height": height,
        "mime": mime,
        "sha1": f"sha-{name}",
        "author": "Ann",
        "licence_code": "cc-by-sa-4.0",
        "licence_name": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": "Ann, CC BY-SA 4.0, via Wikimedia Commons",
        "quality_marks": list(marks),
        "usable": usable,
        "flags": sorted(flags),
    }


def write_b5(
    root: Path,
    *,
    destinations,
    listings,
    candidates,
    coverage=None,
    country_filter="IN",
):
    """Write `enrich/` and `photos/` folders under root; returns (photos_folder, enrich_folder)."""
    enrich = write_enrich_output(
        root / "enrich" / "20261101",
        destinations=destinations,
        listings=listings,
        country_filter=country_filter,
    )
    photos = root / "photos" / "20261101"
    photos.mkdir(parents=True)
    path = photos / "photo_candidates.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in candidates), encoding="utf-8")
    enrich_manifest = enrich / "manifest.json"
    manifest = {
        "country_filter": country_filter,
        "dump_date": "20261101",
        "enrich_manifest_sha256": sha(enrich_manifest),
        "inputs_sha256": {
            "destination_facts.jsonl": sha(enrich / "destination_facts.jsonl"),
            "listing_facts.jsonl": sha(enrich / "listing_facts.jsonl"),
        },
        "outputs_sha256": {"photo_candidates.jsonl": sha(path)},
        "photo_rules_version": 1,
        "totals": {"coverage": coverage or {"all": {}, "by_country": {}}},
    }
    (photos / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return photos, enrich


def dest(page_id, title="X", iso="IN"):
    return destination_row(page_id, title, iso=iso, qid=f"Q{page_id}")


def listing(page_id, ordinal, key, kind="see", iso="IN"):
    return listing_fact(page_id, ordinal, key, kind=kind, iso=iso, qid=f"Q{page_id}{ordinal}")
