import json
from datetime import date

import pytest

from catalogue.blocks.check_run import run_auto_check
from catalogue.errors import PipelineError
from catalogue.prices.run import run_price_bands
from tests.blocksfixture import listing_row, sha
from tests.checkfixture import OWNERS, make_blocks
from tests.test_prices_pricing import entry

TODAY = date(2026, 11, 14)
JAIPUR = [("Amer Fort", "religious"), ("Panna Meena", "sight"), ("Jal Mahal", "sight")]
PRICE_DEFAULTS = {
    "price": "",
    "price_status": "empty",
    "price_is_free": False,
    "price_entries": [],
    "lastedit": "",
    "lastedit_date": None,
    "lastedit_status": "missing",
    "lastedit_precision": None,
    "lastedit_year": None,
    "source_url": "https://example.org/x",
    "licence": "CC BY-SA 4.0",
    "attribution": "Wikivoyage contributors",
}


def priced(name, entries, *, text="text", edit="2026-03-04", status="parsed"):
    return {
        "price": text,
        "price_status": status,
        "price_entries": entries,
        "lastedit": edit or "",
        "lastedit_date": edit,
        "lastedit_status": "parsed" if edit else "missing",
        "lastedit_precision": "day" if edit else None,
        "lastedit_year": int(edit[:4]) if edit else None,
    }


def write_listings(tmp_path, by_name, extra=()):
    built = tmp_path / "built" / "listings" / "listings.jsonl"
    rows = [json.loads(line) for line in built.read_text(encoding="utf-8").splitlines()]
    rows += list(extra)
    for row in rows:
        row.update(PRICE_DEFAULTS)
        row.update(by_name.get(row["name"], {}))
    folder = tmp_path / "priced"
    folder.mkdir()
    path = folder / "listings.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (folder / "manifest.json").write_text(
        json.dumps({"country_filter": "IN", "outputs_sha256": {"listings.jsonl": sha(path)}}),
        encoding="utf-8",
    )
    return folder


def eat_rows(prices):
    return [
        listing_row(100 + i, f"Cafe {i}", kind="eat", owner=OWNERS["jaipur"])
        for i in range(len(prices))
    ]


@pytest.fixture
def world(tmp_path):
    blocks = make_blocks(tmp_path, {"jaipur": JAIPUR})
    checks = run_auto_check(
        blocks_folder=blocks,
        out_dir=tmp_path / "checks",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
    ).output_dir
    eats = (100, 200, 300)
    by_name = {
        "Amer Fort": priced(
            "Amer Fort",
            [entry(50, audience="resident"), entry(300, audience="foreigner")],
            text="Rs 50 Indians, Rs 300 foreigners",
        ),
        "Panna Meena": priced("Panna Meena", [entry(80)], edit="2019-01-01", text="Rs 80"),
        "Jal Mahal": priced("Jal Mahal", [entry(30)], status="partial", text="Rs 30, odd note"),
    }
    for i, value in enumerate(eats):
        by_name[f"Cafe {i}"] = priced(f"Cafe {i}", [entry(value)])
    listings = write_listings(tmp_path, by_name, eat_rows(eats))
    return {"tmp": tmp_path, "blocks": blocks, "checks": checks, "listings": listings}


def run(world, **kw):
    return run_price_bands(
        listings_folder=world["listings"],
        blocks_folder=world["blocks"],
        checks_folder=world["checks"],
        out_dir=world["tmp"] / "prices",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        **kw,
    )


def read(result, name):
    text = (result.output_dir / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_only_the_fresh_price_is_exact_and_keeps_its_tiers_and_source(world):
    result = run(world)
    rows = {r["listing_key"]: r for r in read(result, "listing_prices.jsonl")}
    amer = rows["see:amer-fort"]
    assert amer["status"] == "exact"
    assert amer["tiers"] == {
        "resident": {"low_minor": 5000, "high_minor": 5000},
        "foreigner": {"low_minor": 30000, "high_minor": 30000},
    }
    assert amer["price_text"] == "Rs 50 Indians, Rs 300 foreigners"
    assert amer["licence"] == "CC BY-SA 4.0" and amer["attribution"]
    old = rows["see:panna-meena"]
    assert old["status"] == "not_fresh" and "tiers" not in old and old["age"] == "older"
    assert rows["see:jal-mahal"]["status"] == "unreadable" and "tiers" not in rows["see:jal-mahal"]


def test_the_eat_band_is_written_with_its_counts(world):
    bands = read(run(world), "city_price_bands.jsonl")
    eat = next(b for b in bands if b["kind"] == "eat")
    assert eat["label"] == "approximate" and eat["median_minor"] == 20000
    assert (eat["n_fresh"], eat["n_older"], eat["n_undated"]) == (3, 0, 0)
    assert eat["owner_title"] == "Jaipur"


def test_every_block_estimate_is_labelled_and_has_a_basis(world):
    blocks = read(run(world), "block_costs.jsonl")
    assert len(blocks) == 1
    block = blocks[0]
    assert block["label"] == "estimate" and block["check_status"] == "checked"
    foreigner = block["foreigner"]
    assert foreigner["currency"] == "INR"
    assert foreigner["stop_basis"] == ["exact", "unknown", "unknown"]
    assert foreigner["food"]["meals"] == 1
    assert foreigner["total_low_minor"] == 30000 + foreigner["food"]["low_minor"]
    assert "local transport not included" in foreigner["basis"]
    assert block["resident"]["exact"]["low_minor"] == 5000


def test_days_come_from_checked_blocks_and_add_their_blocks(world):
    result = run(world)
    days = read(result, "day_costs.jsonl")
    blocks = read(result, "block_costs.jsonl")
    assert len(blocks) == 1
    assert days == []  # one half-day block is not a whole day


def test_report_and_sample_say_estimate_and_count_what_is_unknown(world):
    result = run(world)
    report = (result.output_dir / "report.md").read_text(encoding="utf-8")
    sample = (result.output_dir / "cost_sample.md").read_text(encoding="utf-8")
    assert "estimate" in report and "Check on official site" in report
    assert "Amer Fort" in sample and "estimate" in sample
    assert result.totals["blocks"] == 1 and result.totals["listings_exact"] == 4
    assert result.totals["listings_fresh_unreadable"] == 1
    assert "edited within two years but not read" in report


def test_a_second_run_is_identical(world):
    first = run(world)
    before = {p.name: p.read_bytes() for p in first.output_dir.iterdir()}
    second = run(world)
    assert {p.name: p.read_bytes() for p in second.output_dir.iterdir()} == before


def test_the_manifest_records_inputs_outputs_and_versions(world):
    result = run(world)
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["price_rules_version"] == 1 and manifest["country_filter"] == "IN"
    assert set(manifest["inputs_sha256"]) >= {"listings.jsonl", "day_blocks.jsonl"}
    for name, digest in manifest["outputs_sha256"].items():
        assert sha(result.output_dir / name) == digest


def test_an_edited_listings_file_is_refused(world):
    with (world["listings"] / "listings.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(PipelineError, match="manifest"):
        run(world)


def test_checks_built_from_other_blocks_rules_are_refused(world):
    path = world["checks"] / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["blocks_rules_version"] = 99
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="auto-check"):
        run(world)


def test_a_different_country_filter_is_refused(world):
    with pytest.raises(PipelineError, match="country"):
        run_price_bands(
            listings_folder=world["listings"],
            blocks_folder=world["blocks"],
            checks_folder=world["checks"],
            out_dir=world["tmp"] / "prices",
            dump_date="20261101",
            today=TODAY,
            country_iso=None,
        )


def test_no_output_names_a_listing_price_text_in_the_log_summary(world):
    assert "Rs 50" not in run(world).summary()


def test_a_from_price_is_counted_apart_and_never_shown_as_exact(tmp_path):
    blocks = make_blocks(tmp_path, {"jaipur": JAIPUR})
    checks = run_auto_check(
        blocks_folder=blocks,
        out_dir=tmp_path / "checks",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
    ).output_dir
    by_name = {"Amer Fort": priced("Amer Fort", [entry(500, qualifier="from")], text="From Rs 500")}
    world = {
        "tmp": tmp_path,
        "blocks": blocks,
        "checks": checks,
        "listings": write_listings(tmp_path, by_name),
    }
    result = run(world)
    rows = {r["listing_key"]: r for r in read(result, "listing_prices.jsonl")}
    assert rows["see:amer-fort"]["status"] == "qualified" and "tiers" not in rows["see:amer-fort"]
    assert result.totals["listings_qualified"] == 1 and result.totals["listings_exact"] == 0
    assert read(result, "block_costs.jsonl")[0]["foreigner"]["stop_basis"][0] == "unknown"


def make_world(tmp_path, by_name, extra=()):
    blocks = make_blocks(tmp_path, {"jaipur": JAIPUR})
    checks = run_auto_check(
        blocks_folder=blocks,
        out_dir=tmp_path / "checks",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
    ).output_dir
    return {
        "tmp": tmp_path,
        "blocks": blocks,
        "checks": checks,
        "listings": write_listings(tmp_path, by_name, extra),
    }


def test_a_price_in_another_currency_than_the_citys_is_never_exact(tmp_path):
    by_name = {"Amer Fort": priced("Amer Fort", [entry(10, currency="USD")], text="US$10")}
    result = run(make_world(tmp_path, by_name))
    row = next(
        r for r in read(result, "listing_prices.jsonl") if r["listing_key"] == "see:amer-fort"
    )
    assert row["status"] == "other_currency" and "tiers" not in row
    assert result.totals["listings_exact"] == 0
    assert read(result, "block_costs.jsonl")[0]["foreigner"]["stop_basis"][0] == "unknown"


def test_a_block_with_only_a_food_figure_has_no_data_for_its_stops(tmp_path):
    eats = (100, 200, 300)
    by_name = {f"Cafe {i}": priced(f"Cafe {i}", [entry(v)]) for i, v in enumerate(eats)}
    result = run(make_world(tmp_path, by_name, eat_rows(eats)))
    block = read(result, "block_costs.jsonl")[0]
    assert block["foreigner"]["food"] is not None and block["foreigner"]["total_low_minor"]
    assert result.totals["blocks_by_completeness"]["foreigner"]["no_data"] == 1


def test_a_damaged_listing_row_is_one_clear_error(tmp_path):
    world = make_world(tmp_path, {})
    path = world["listings"] / "listings.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    del rows[0]["listing_key"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    manifest = world["listings"] / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputs_sha256"]["listings.jsonl"] = sha(path)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PipelineError, match="damaged"):
        run(world)
