"""Builders for B10 tests: block and stop rows in B8's output shape, and a real B8 output folder."""

from datetime import date

from catalogue.blocks.run import run_draft_blocks
from tests.blocksfixture import fact_row, listing_row, write_inputs


def stop_row(
    block_id,
    position,
    name,
    *,
    kind=None,
    unesco=False,
    hop_method=None,
    hop_minutes=0.0,
    key=None,
):
    return {
        "block_id": block_id,
        "position": position,
        "name": name,
        "kind": kind,
        "unesco": unesco,
        "listing_key": key or f"see:{name.lower().replace(' ', '-')}",
        "page_id": 1,
        "ordinal": position,
        "hop_method": hop_method if hop_method is not None else ("walk" if position > 1 else ""),
        "hop_minutes": hop_minutes if position > 1 else 0.0,
        "hours_known": False,
        "has_photo": False,
        "source": "listing",
    }


def block_row(block_id="b1", *, slot="morning", slot_basis="kind", minutes=120, **kw):
    row = {
        "block_id": block_id,
        "owner_page_id": 1,
        "owner_slug": "jaipur",
        "owner_title": "Jaipur",
        "slot": slot,
        "slot_basis": slot_basis,
        "day_trip": False,
        "status": "draft",
        "method": "estimate",
        "title": "Morning around X",
        "intro": "X · about 2 h (estimate)",
        "minutes": minutes,
        "hours_unknown_count": 2,
        "photo_count": 0,
        "rules_version": 2,
        "dump_date": "20261101",
    }
    row.update(kw)
    return row


def stops_for(block_id, *names, kinds=None):
    kinds = kinds or {}
    return [stop_row(block_id, i, n, kind=kinds.get(n)) for i, n in enumerate(names, start=1)]


OWNERS = {
    "jaipur": (1, "jaipur", "Jaipur", "city"),
    "agra": (2, "agra", "Agra", "city"),
    "varanasi": (3, "varanasi", "Varanasi", "city"),
}


def make_blocks(tmp_path, cities):
    """Run the real draft-blocks job. cities: {slug: [(name, kind), ...]}, stops 0.2 km apart.
    Returns the blocks output folder."""
    rows, facts = [], []
    ordinal = 0
    for slug, stops in cities.items():
        for i, (name, kind) in enumerate(stops):
            ordinal += 1
            row = listing_row(ordinal, name, km_north=i * 0.2, owner=OWNERS[slug])
            rows.append(row)
            facts.append(fact_row(row, kind=kind))
    lis, enr, pho = write_inputs(tmp_path / "built", rows, facts)
    result = run_draft_blocks(
        listings_folder=lis,
        enrich_folder=enr,
        photos_folder=pho,
        discover_folder=lis.parent / "discover",
        summaries_folder=lis.parent / "summaries",
        out_dir=tmp_path / "blocks",
        dump_date="20261101",
        today=date(2026, 11, 14),
        country_iso="IN",
    )
    return result.output_dir
