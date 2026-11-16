import json
from datetime import date
from pathlib import Path

import pytest

from catalogue.errors import PipelineError
from catalogue.prices.fees import COLUMNS, OfficialFee, load_official_fees
from tests.test_prices_run import read, run, world  # noqa: F401 - the `world` fixture is reused

HEADER = ",".join(COLUMNS)
GOOD = (
    "jaipur,see:amer-fort,Amber Fort,INR,100,500,"
    "https://example.gov.in/amber,2026-11-10,general ticket"
)


def write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "fees.csv"
    path.write_text("\n".join([HEADER, *lines]) + "\n", encoding="utf-8")
    return path


def test_a_fee_row_is_read_with_its_source_and_date(tmp_path):
    fees = load_official_fees(write(tmp_path, GOOD), today=date(2026, 11, 16))

    assert fees[("jaipur", "see:amer-fort")] == OfficialFee(
        owner_slug="jaipur",
        listing_key="see:amer-fort",
        place="Amber Fort",
        currency="INR",
        resident="100",
        foreigner="500",
        source_url="https://example.gov.in/amber",
        date_read=date(2026, 11, 10),
        note="general ticket",
    )


def test_a_missing_file_is_no_fees(tmp_path):
    assert load_official_fees(tmp_path / "none.csv", today=date(2026, 11, 16)) == {}


@pytest.mark.parametrize(
    "line",
    [
        "jaipur,see:a,A,INR,100,500,http://example.gov.in/x,2026-11-10,n",  # not https
        "jaipur,see:a,A,INR,100,500,,2026-11-10,n",  # no source
        "jaipur,see:a,A,INR,100,500,https://example.gov.in/x,,n",  # no date
        "jaipur,see:a,A,INR,100,500,https://example.gov.in/x,2027-01-01,n",  # in the future
        "jaipur,see:a,A,INR,100,500,https://example.gov.in/x,16/11/2026,n",  # not ISO
        "jaipur,see:a,A,INR,abc,500,https://example.gov.in/x,2026-11-10,n",  # not an amount
        "jaipur,see:a,A,INR,-1,500,https://example.gov.in/x,2026-11-10,n",  # negative
        "jaipur,see:a,A,inr,100,500,https://example.gov.in/x,2026-11-10,n",  # not a currency code
        "jaipur,see:a,A,INR,100,,https://example.gov.in/x,2026-11-10,n",  # one tier missing
        ",see:a,A,INR,100,500,https://example.gov.in/x,2026-11-10,n",  # no city
    ],
)
def test_a_row_that_cannot_be_trusted_is_one_clear_error(tmp_path, line):
    with pytest.raises(PipelineError, match="monument_fees.csv"):
        load_official_fees(write(tmp_path, line), today=date(2026, 11, 16))


def test_the_same_place_twice_is_refused(tmp_path):
    with pytest.raises(PipelineError, match="twice"):
        load_official_fees(write(tmp_path, GOOD, GOOD), today=date(2026, 11, 16))


def test_wrong_columns_are_refused(tmp_path):
    path = tmp_path / "fees.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="columns"):
        load_official_fees(path, today=date(2026, 11, 16))


@pytest.fixture
def priced_world(world):  # noqa: F811
    return world


def test_an_official_fee_replaces_a_listing_price_as_an_exact_price(priced_world, tmp_path):
    path = write(tmp_path, GOOD)

    result = run(priced_world, fees_path=path)
    amer = {r["listing_key"]: r for r in read(result, "listing_prices.jsonl")}["see:amer-fort"]

    assert amer["status"] == "exact" and amer["fee_source"] == "official"
    assert amer["tiers"] == {
        "resident": {"low_minor": 10000, "high_minor": 10000},
        "foreigner": {"low_minor": 50000, "high_minor": 50000},
    }
    assert amer["source_url"] == "https://example.gov.in/amber"
    assert amer["lastedit_date"] == "2026-11-10"
    assert amer["attribution"] and amer["licence"]
    assert result.totals["official_fees_applied"] == 1
    assert result.totals["official_fees_unmatched"] == 0


def test_an_official_fee_beats_a_conflicting_wikivoyage_price_and_feeds_the_block_cost(
    priced_world, tmp_path
):
    plain = read(run(priced_world, fees_path=tmp_path / "none.csv"), "block_costs.jsonl")
    official = read(run(priced_world, fees_path=write(tmp_path, GOOD)), "block_costs.jsonl")

    assert plain != official


def test_a_fee_for_a_place_that_is_not_in_the_listings_is_counted_not_applied(
    priced_world, tmp_path
):
    stray = GOOD.replace("see:amer-fort", "see:no-such-place")

    result = run(priced_world, fees_path=write(tmp_path, stray))

    assert result.totals["official_fees_applied"] == 0
    assert result.totals["official_fees_unmatched"] == 1
    assert "see:no-such-place" in (result.output_dir / "report.md").read_text(encoding="utf-8")


def test_the_fee_file_is_part_of_the_manifest_inputs(priced_world, tmp_path):
    first = run(priced_world, fees_path=write(tmp_path, GOOD))
    manifest = json.loads((first.output_dir / "manifest.json").read_text(encoding="utf-8"))

    assert "monument_fees.csv" in manifest["inputs_sha256"]


def test_a_fee_in_another_currency_than_the_citys_is_not_exact(priced_world, tmp_path):
    usd = GOOD.replace(",INR,", ",USD,")

    result = run(priced_world, fees_path=write(tmp_path, usd))
    amer = {r["listing_key"]: r for r in read(result, "listing_prices.jsonl")}["see:amer-fort"]

    assert amer["status"] != "exact"


def test_a_stale_fee_is_not_exact(priced_world, tmp_path):
    old = GOOD.replace("2026-11-10", "2020-01-01")

    result = run(priced_world, fees_path=write(tmp_path, old))
    amer = {r["listing_key"]: r for r in read(result, "listing_prices.jsonl")}["see:amer-fort"]

    assert amer["status"] == "not_fresh"
