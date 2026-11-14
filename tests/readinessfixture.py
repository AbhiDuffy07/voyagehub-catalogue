"""A tiny hand-built upstream world for B12 tests: every input folder with its manifest and hashes.

Cities (all India, owner page ids): 1 Jaipur = ready + a whole checked day (plannable); 2 Agra =
ready, its places listed under district page 20, one checked morning block only (browse only);
3 Varanasi = no summary, three photographed places, only a flagged and a rejected block (not
ready); 4 Goa is a region (not assessed); 5 Kochi has one place photo and one on a sleep listing,
no time zone, no block (not ready).
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from catalogue.readiness.run import run_readiness

TODAY = date(2026, 11, 15)
PHOTO = {"commons_file": "X.jpg"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return sha(path)


def manifest(folder: Path, files: dict[str, str], country="IN", **extra) -> None:
    (folder / "manifest.json").write_text(
        json.dumps({"country_filter": country, "outputs_sha256": files, **extra}), encoding="utf-8"
    )


def article(pid, title, kind="city", status="usable", country="IN"):
    return {
        "wikivoyage_page_id": pid,
        "title": title,
        "type": kind,
        "status": status,
        "tree_country_iso": country,
    }


def listing(pid, ordinal, owner, kind="see", duplicate_of=None):
    return {
        "page_id": pid,
        "ordinal": ordinal,
        "owner_page_id": owner,
        "kind": kind,
        "duplicate_of": duplicate_of,
    }


def photo(subject, pid, ordinal=0, *, hero=False, has=True):
    return {
        "subject": subject,
        "page_id": pid,
        "ordinal": ordinal,
        "photo": PHOTO if has else None,
        "hero_quality": hero and has,
    }


def block(block_id, owner, slug, slot, basis="kind"):
    return {
        "block_id": block_id,
        "owner_page_id": owner,
        "owner_slug": slug,
        "slot": slot,
        "slot_basis": basis,
    }


def build(tmp_path: Path, *, summaries=(1, 2, 5), blocks_rules=2, checks_rules=2, tag=""):
    root = tmp_path / f"world{tag}"
    discover = root / "discover"
    articles = [
        article(1, "Jaipur"),
        article(2, "Agra", status="guide"),
        article(3, "Varanasi", status="outline"),
        article(4, "Goa", kind="region"),
        article(5, "Kochi"),
        article(20, "Agra Fort District", kind="district"),
    ]
    articles_hash = jsonl(discover / "articles.jsonl", articles)

    listings = [
        *(listing(1, i, 1) for i in (1, 2, 3, 4)),
        *(listing(20, i, 2) for i in (1, 2, 3)),  # a district's listings belong to Agra
        listing(20, 4, 2, duplicate_of="see:x"),  # a duplicate is never a place
        *(listing(3, i, 3) for i in (1, 2, 3)),
        listing(5, 1, 5, kind="sleep"),
        *(listing(5, i, 5) for i in (2,)),
    ]
    lis = root / "listings"
    manifest(
        lis,
        {"listings.jsonl": jsonl(lis / "listings.jsonl", listings)},
        articles_sha256=articles_hash,
    )

    photos = [
        photo("article", 1, hero=True),
        *(photo("listing", 1, i) for i in (1, 2, 3)),
        photo("listing", 1, 4, has=False),
        photo("article", 2),
        *(photo("listing", 20, i, hero=(i == 1)) for i in (1, 2, 3, 4)),
        *(photo("listing", 3, i, hero=True) for i in (1, 2, 3)),
        photo("listing", 5, 1, hero=True),
        photo("listing", 5, 2, hero=True),
    ]
    pho = root / "photos"
    manifest(pho, {"photo_selection.jsonl": jsonl(pho / "photo_selection.jsonl", photos)})

    sums = [
        {"subject": "article", "page_id": pid, "ordinal": 0, "summary": "Text."}
        for pid in summaries
    ]
    sums += [{"subject": "article", "page_id": 3, "ordinal": 0, "summary": ""}]
    sumf = root / "summaries"
    manifest(sumf, {"summaries.jsonl": jsonl(sumf / "summaries.jsonl", sums)})

    zones = [{"wikivoyage_page_id": pid, "timezone": "Asia/Kolkata"} for pid in (1, 2, 3)] + [
        {"wikivoyage_page_id": 5, "timezone": None}
    ]
    tzf = root / "timezones"
    manifest(
        tzf,
        {"destination_timezones.jsonl": jsonl(tzf / "destination_timezones.jsonl", zones)},
        country=None,
    )

    blocks = [
        block("jaipur-full", 1, "jaipur", "full_day"),
        block("agra-am", 2, "agra", "morning"),
        block("varanasi-am", 3, "3", "morning"),  # a slug that is the page id, like the real run
        block("varanasi-pm", 3, "3", "afternoon"),
    ]
    blk = root / "blocks"
    manifest(
        blk,
        {"day_blocks.jsonl": jsonl(blk / "day_blocks.jsonl", blocks)},
        rules_version=blocks_rules,
    )
    checks = [
        {"block_id": "jaipur-full", "check_status": "checked"},
        {"block_id": "agra-am", "check_status": "checked"},
        {"block_id": "varanasi-am", "check_status": "flagged"},
        {"block_id": "varanasi-pm", "check_status": "rejected"},
    ]
    chk = root / "checks"
    manifest(
        chk,
        {"block_checks.jsonl": jsonl(chk / "block_checks.jsonl", checks)},
        blocks_rules_version=checks_rules,
    )

    slugs = root / "slugs.json"
    slugs.write_text(
        json.dumps(
            {
                "entries": {
                    str(i): {"slug": t.lower(), "title": t.title()}
                    for i, t in ((1, "jaipur"), (2, "agra"), (3, "varanasi"), (5, "kochi"))
                }
            }
        ),
        encoding="utf-8",
    )
    return {
        "listings_folder": lis,
        "summaries_folder": sumf,
        "photos_folder": pho,
        "blocks_folder": blk,
        "checks_folder": chk,
        "timezones_folder": tzf,
        "discover_folder": discover,
        "slugs_path": slugs,
        "out_dir": tmp_path / f"out{tag}",
        "dump_date": "20261101",
        "today": TODAY,
        "country_iso": "IN",
    }


def run(tmp_path, **overrides):
    tag = overrides.pop("tag", "")
    kwargs = build(
        tmp_path,
        tag=tag,
        **{
            k: overrides.pop(k)
            for k in list(overrides)
            if k in ("summaries", "blocks_rules", "checks_rules")
        },
    )
    kwargs.update(overrides)
    return run_readiness(**kwargs), kwargs


def rows(result):
    path = result.output_dir / "city_readiness.jsonl"
    return {
        r["wikivoyage_page_id"]: r
        for r in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    }
