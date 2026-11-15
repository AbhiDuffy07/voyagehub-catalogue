"""A tiny upstream world for B13 tests, built on the B12 fixture.

Cities: 1 Jaipur (published, slug "jaipur", plannable, with credits), 2 Agra (published, browse
only), 3 Varanasi and 5 Kochi (not ready, never published). City 6 has no slug and is not ready.
Photos and summaries carry author, licence and link.
"""

import json
from pathlib import Path

from catalogue.readiness.run import run_readiness
from tests import readinessfixture as rf

CREDIT_PHOTO = {
    "author": "Gerd Eichmann",
    "attribution": "Gerd Eichmann, CC BY-SA 4.0, via Wikimedia Commons",
    "licence_name": "CC BY-SA 4.0",
    "licence_url": "https://creativecommons.org/licenses/by-sa/4.0",
    "description_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
    "commons_file": "X.jpg",
    "width": 4000,
    "height": 3000,
    "thumb_url": "https://thumb.example/X.jpg",
    "thumb_width": 1280,
}


def _summary(subject, pid, ordinal, text, key=None):
    return {
        "subject": subject,
        "page_id": pid,
        "ordinal": ordinal,
        "listing_key": key,
        "summary": text,
        "attribution": 'From the Wikipedia article "X" by Wikipedia contributors, CC BY-SA 4.0',
        "licence": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "permalink": f"https://en.wikipedia.org/w/index.php?title=X{pid}&oldid=1",
    }


def build(tmp_path: Path) -> dict:
    kwargs = rf.build(tmp_path, summaries=(1, 2, 5, 6))
    root = tmp_path / "world"
    discover = root / "discover"
    articles = [
        rf.article(1, "Jaipur"),
        rf.article(2, "Agra", status="guide"),
        rf.article(3, "Varanasi", status="outline"),
        rf.article(5, "Kochi"),
        rf.article(6, "Nameless"),
    ]
    articles_hash = rf.jsonl(discover / "articles.jsonl", articles)

    listings = [
        {
            **rf.listing(1, i, 1),
            "name": f"Fort {i}",
            "lat": 26.9 + i / 100,
            "long": 75.8,
            "listing_key": f"see:fort-{i}",
        }
        for i in (1, 2, 3, 4)
    ] + [
        {
            **rf.listing(20, i, 2),
            "name": f"Tomb {i}",
            "lat": 27.1,
            "long": 78.0,
            "listing_key": f"see:tomb-{i}",
        }
        for i in (1, 2, 3)
    ]
    lis = root / "listings"
    rf.manifest(
        lis,
        {"listings.jsonl": rf.jsonl(lis / "listings.jsonl", listings)},
        articles_sha256=articles_hash,
    )

    def ph(subject, pid, ordinal=0, hero=False):
        row = rf.photo(subject, pid, ordinal, hero=hero)
        row["photo"] = {**CREDIT_PHOTO, "commons_file": f"P{pid}-{ordinal}.jpg"}
        return row

    photos = [
        ph("article", 1, hero=True),
        *(ph("listing", 1, i) for i in (1, 2, 3)),
        {
            **rf.photo("listing", 1, 4),
            "photo": {"commons_file": "bad.jpg", "thumb_url": "https://t/x"},
        },
        *(ph("listing", 20, i, hero=(i == 1)) for i in (1, 2, 3)),
    ]
    pho = root / "photos"
    rf.manifest(pho, {"photo_selection.jsonl": rf.jsonl(pho / "photo_selection.jsonl", photos)})

    sums = [
        _summary("article", 1, 0, "Jaipur is pink."),
        _summary("article", 2, 0, "Agra has the Taj."),
        _summary("listing", 1, 1, "Fort one.", "see:fort-1"),
    ]
    sumf = root / "summaries"
    rf.manifest(sumf, {"summaries.jsonl": rf.jsonl(sumf / "summaries.jsonl", sums)})

    kwargs["summaries_folder"] = sumf
    blk = root / "blocks"
    blocks = [
        {
            **rf.block("jaipur-full", 1, "1", "full_day"),
            "title": "A day in Jaipur",
            "intro": "Fort 1 → Fort 2",
            "minutes": 300,
            "stop_count": 2,
            "walk_km": 1.2,
            "day_trip": False,
        },
        {**rf.block("agra-am", 2, "2", "morning"), "title": "Morning", "intro": "", "minutes": 90},
        {**rf.block("jaipur-flagged", 1, "1", "morning"), "title": "Held back", "minutes": 60},
    ]
    stops = [
        {
            "block_id": "jaipur-full",
            "position": 1,
            "listing_key": "see:fort-1",
            "page_id": 1,
            "ordinal": 1,
            "name": "Fort 1",
            "lat": 26.91,
            "long": 75.8,
            "minutes_min": 30,
            "minutes_max": 60,
        },
        {
            "block_id": "jaipur-full",
            "position": 2,
            "listing_key": "see:fort-2",
            "page_id": 1,
            "ordinal": 2,
            "name": "Fort 2",
            "lat": 26.92,
            "long": 75.8,
            "minutes_min": 30,
            "minutes_max": 60,
        },
    ]
    rf.manifest(
        blk,
        {
            "day_blocks.jsonl": rf.jsonl(blk / "day_blocks.jsonl", blocks),
            "day_block_stops.jsonl": rf.jsonl(blk / "day_block_stops.jsonl", stops),
        },
        rules_version=2,
    )
    chk = root / "checks"
    checks = [
        {"block_id": "jaipur-full", "check_status": "checked"},
        {"block_id": "agra-am", "check_status": "checked"},
        {"block_id": "jaipur-flagged", "check_status": "flagged"},
    ]
    rf.manifest(
        chk,
        {"block_checks.jsonl": rf.jsonl(chk / "block_checks.jsonl", checks)},
        blocks_rules_version=2,
        inputs_sha256={"day_blocks.jsonl": rf.sha(blk / "day_blocks.jsonl")},
    )
    kwargs["blocks_folder"], kwargs["checks_folder"] = blk, chk

    tzf = root / "timezones"
    zones = [
        {
            "wikivoyage_page_id": pid,
            "timezone": "Asia/Kolkata",
            "timezone_basis": "coordinates",
            "timezone_attribution": "tz-builder",
        }
        for pid in (1, 2, 6)
    ]
    rf.manifest(
        tzf,
        {"destination_timezones.jsonl": rf.jsonl(tzf / "destination_timezones.jsonl", zones)},
        country=None,
    )

    slugs = root / "slugs.json"
    slugs.write_text(
        json.dumps(
            {
                "entries": {
                    str(i): {"slug": t, "title": t.title()}
                    for i, t in ((1, "jaipur"), (2, "agra"), (3, "varanasi"), (5, "kochi"))
                }
            }
        ),
        encoding="utf-8",
    )
    kwargs["slugs_path"] = slugs

    result = run_readiness(**kwargs)

    prices = root / "prices"
    estimate = {
        "basis": "2 stop(s) with no price",
        "complete": False,
        "currency": "INR",
        "total_low_minor": None,
        "total_high_minor": None,
        "note": "Check on official site",
        "transport": "not included: no fare data",
    }
    bands = [
        {
            "owner_page_id": 1,
            "kind": "sleep",
            "group": "foreigner",
            "basis": "all",
            "currency": "INR",
            "low_minor": 1000,
            "high_minor": 5000,
            "median_minor": 2000,
            "label": "approximate",
            "n_prices": 7,
            "n_fresh": 0,
        }
    ]
    costs = [
        {
            "block_id": block_id,
            "label": "estimate",
            "foreigner": estimate,
            "resident": estimate,
        }
        for block_id in ("jaipur-full", "jaipur-flagged")
    ]
    days = [
        {
            "day_id": "day:jaipur-full",
            "block_ids": ["jaipur-full"],
            "label": "estimate",
            "foreigner": estimate,
            "resident": estimate,
        },
        {
            "day_id": "day:with-flagged",
            "block_ids": ["jaipur-full", "jaipur-flagged"],
            "label": "estimate",
            "foreigner": estimate,
            "resident": estimate,
        },
        {
            "day_id": "day:two-cities",
            "block_ids": ["jaipur-full", "agra-am"],
            "label": "estimate",
            "foreigner": estimate,
            "resident": estimate,
        },
    ]
    rf.manifest(
        prices,
        {
            "city_price_bands.jsonl": rf.jsonl(prices / "city_price_bands.jsonl", bands),
            "block_costs.jsonl": rf.jsonl(prices / "block_costs.jsonl", costs),
            "day_costs.jsonl": rf.jsonl(prices / "day_costs.jsonl", days),
        },
        inputs_sha256={
            "listings.jsonl": rf.sha(lis / "listings.jsonl"),
            "day_blocks.jsonl": rf.sha(blk / "day_blocks.jsonl"),
            "day_block_stops.jsonl": rf.sha(blk / "day_block_stops.jsonl"),
            "block_checks.jsonl": rf.sha(chk / "block_checks.jsonl"),
        },
    )
    return {
        "readiness_folder": result.output_dir,
        "listings_folder": lis,
        "summaries_folder": sumf,
        "photos_folder": pho,
        "blocks_folder": blk,
        "checks_folder": chk,
        "timezones_folder": tzf,
        "prices_folder": prices,
        "out_dir": tmp_path / "publish",
        "dump_date": "20261101",
        "today": rf.TODAY,
        "country_iso": "IN",
    }
