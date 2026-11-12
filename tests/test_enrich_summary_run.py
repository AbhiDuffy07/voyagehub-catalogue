import csv
import json
from datetime import date

import pytest
import respx

from catalogue.enrich.summary import SummaryRules
from catalogue.enrich.summary_run import run_wikipedia_summary
from catalogue.errors import PipelineError
from tests.apifakes import fake_wikipedia_summaries
from tests.enrichfixture import destination_row, listing_fact, write_enrich_output

ENWIKI = "https://en.wikipedia.org/w/api.php"
TODAY = date(2026, 11, 12)
RULES = SummaryRules(version=1, very_short_chars=40, reasons={"very_short_chars": "test"})

LONG = "Jaipur is the capital of Rajasthan, a city of palaces.\nIt is called the Pink City."


def wiki(extract=LONG, revid=100, disambiguation=False):
    return {
        "extract": extract,
        "revid": revid,
        "timestamp": "2026-11-02T10:00:00Z",
        "disambiguation": disambiguation,
    }


PAGES = {
    "Jaipur": wiki(),
    "Amer Fort": wiki("Amer Fort is a fort in Amer, near Jaipur, Rajasthan, India.", 200),
    "Mercury": wiki("Mercury may refer to:", 300, disambiguation=True),
    "Stub": wiki("A stub.", 400),
    "Blank": wiki("", 500),
    "Agra": wiki("Agra is a city on the Yamuna river in Uttar Pradesh, India.\nSecond.", 600),
}

DESTINATIONS = [
    destination_row(1, "Jaipur", enwiki="Jaipur", qid="Q66485"),
    destination_row(2, "Agra", enwiki="Agra"),
    destination_row(3, "Nowhere", enwiki=None),
    destination_row(4, "Lost", enwiki="Page That Is Gone", iso=None),
]
LISTINGS = [
    listing_fact(1, 1, "see:amber-fort", enwiki="Amer Fort"),
    listing_fact(1, 2, "do:jaipur-itself", kind="do", enwiki="Jaipur"),
    listing_fact(1, 3, "eat:cafe", kind="eat", enwiki="Jaipur"),
    listing_fact(1, 4, "see:no-link", enwiki=None),
    listing_fact(1, 5, "see:planet", enwiki="Mercury"),
    listing_fact(1, 6, "see:stub", enwiki="Stub"),
    listing_fact(1, 7, "see:blank", enwiki="Blank"),
    listing_fact(1, 8, "see:old-name", enwiki="Old Name"),
]


@pytest.fixture
def enrich_folder(tmp_path):
    return write_enrich_output(
        tmp_path / "enrich" / "20261101-IN", destinations=DESTINATIONS, listings=LISTINGS
    )


def run(client, folder, tmp_path, **kw):
    return run_wikipedia_summary(
        client,
        enrich_folder=folder,
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
        rules=RULES,
        **kw,
    )


def mock(seen=None):
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia_summaries(PAGES, redirects={"Old Name": "Amer Fort"}, seen=seen)
    )


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def table(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def result(client, enrich_folder, tmp_path):
    with respx.mock:
        mock()
        return run(client, enrich_folder, tmp_path)


def rows(result):
    found = jsonl(result.output_dir / "summaries.jsonl")
    return {(r["page_id"], r["ordinal"]): r for r in found}


def test_output_folder_carries_the_country_filter(result):
    assert result.output_dir.name == "20261101-IN"


def test_only_articles_and_see_do_listings_with_a_wikipedia_title_get_a_row(result):
    keys = set(rows(result))
    # articles use ordinal 0; eat listings and rows without a title are left out
    assert keys == {(1, 0), (2, 0), (4, 0), (1, 1), (1, 2), (1, 5), (1, 6), (1, 7), (1, 8)}


def test_an_ordinary_summary_carries_text_links_revision_and_credit(result):
    row = rows(result)[(1, 0)]
    assert row["subject"] == "article" and row["listing_key"] is None
    assert row["lead_text"] == LONG
    assert row["summary"] == "Jaipur is the capital of Rajasthan, a city of palaces."
    assert row["wikipedia_title"] == "Jaipur" and row["requested_title"] == "Jaipur"
    assert row["url"] == "https://en.wikipedia.org/wiki/Jaipur"
    assert row["permalink"] == "https://en.wikipedia.org/w/index.php?title=Jaipur&oldid=100"
    assert row["history_url"].endswith("title=Jaipur&action=history")
    assert (row["revision_id"], row["revision_timestamp"]) == (100, "2026-11-02T10:00:00Z")
    assert row["licence"] == "CC BY-SA 4.0" and "creativecommons.org" in row["licence_url"]
    assert "Wikipedia contributors" in row["attribution"] and "excerpt" in row["attribution"]
    assert row["country_iso"] == "IN" and row["wikidata_id"] == "Q66485"
    assert row["read_at"] == "2026-11-12" and row["dump_date"] == "2026-11-01"
    assert row["flags"] == []


def test_a_listing_row_has_its_key_and_shares_a_fetch_with_the_article(
    client, enrich_folder, tmp_path
):
    seen = []
    with respx.mock:
        mock(seen=seen)
        done = run(client, enrich_folder, tmp_path)
    row = rows(done)[(1, 2)]
    assert row["subject"] == "listing" and row["listing_key"] == "do:jaipur-itself"
    assert row["lead_text"] == LONG
    asked = [t for p in seen for t in p["titles"].split("|")]
    assert asked.count("Jaipur") == 1 and len(asked) == len(set(asked))


def test_a_disambiguation_page_gets_a_flag_and_no_text(result):
    row = rows(result)[(1, 5)]
    assert row["flags"] == ["disambiguation"]
    assert row["lead_text"] is None and row["summary"] is None


def test_a_missing_page_gets_a_flag_and_no_text(result):
    row = rows(result)[(4, 0)]
    assert row["flags"] == ["missing_page"] and row["lead_text"] is None
    assert row["country_iso"] is None


def test_an_empty_lead_gets_a_flag_and_no_text(result):
    row = rows(result)[(1, 7)]
    assert row["flags"] == ["empty_extract"] and row["lead_text"] is None
    assert row["wikipedia_title"] == "Blank"


def test_a_very_short_lead_is_flagged_but_kept(result):
    row = rows(result)[(1, 6)]
    assert row["flags"] == ["very_short"] and row["lead_text"] == "A stub."


def test_a_redirect_is_flagged_and_credits_the_article_that_was_read(result):
    row = rows(result)[(1, 8)]
    assert row["flags"] == ["redirected"]
    assert (row["requested_title"], row["wikipedia_title"]) == ("Old Name", "Amer Fort")
    assert row["url"] == "https://en.wikipedia.org/wiki/Amer_Fort"
    assert row["lead_text"].startswith("Amer Fort is a fort")


def test_rows_are_sorted_by_page_then_ordinal(result):
    keys = [(r["page_id"], r["ordinal"]) for r in jsonl(result.output_dir / "summaries.jsonl")]
    assert keys == sorted(keys)


def test_country_and_flag_tables(result):
    by_country = {
        (r["country_iso"], r["subject"]): r
        for r in table(result.output_dir / "summaries_by_country.csv")
    }
    assert by_country[("IN", "listings")]["rows"] == "6"
    assert by_country[("IN", "articles")]["with_text"] == "2"
    assert by_country[("(none)", "articles")]["rows"] == "1"
    flags = {
        (r["subject"], r["flag"]): int(r["count"])
        for r in table(result.output_dir / "flags_by_country.csv")
        if r["country_iso"] == "IN"
    }
    assert flags[("listings", "disambiguation")] == 1 and flags[("listings", "very_short")] == 1


def test_manifest_records_inputs_rules_totals_and_output_hashes(result, enrich_folder):
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["country_filter"] == "IN" and manifest["dump_date"] == "20261101"
    assert manifest["summary_rules_version"] == 1
    assert len(manifest["enrich_manifest_sha256"]) == 64
    assert set(manifest["inputs_sha256"]) == {"destination_facts.jsonl", "listing_facts.jsonl"}
    assert "summaries.jsonl" in manifest["outputs_sha256"]
    totals = manifest["totals"]
    assert totals["rows"] == 9 and totals["articles"] == 3 and totals["listings"] == 6
    assert totals["with_text"] == 6
    assert totals["flags"]["missing_page"] == 1


def test_the_report_shows_lead_lengths_for_setting_the_threshold(result):
    text = (result.output_dir / "report.md").read_text(encoding="utf-8")
    assert "Lead length" in text and "median" in text


def test_the_summary_has_counts_and_no_text(result):
    text = result.summary()
    assert "9" in text and "Pink City" not in text and "example.invalid" not in text


def test_a_second_run_gives_identical_files(client, enrich_folder, tmp_path, result):
    first = (result.output_dir / "manifest.json").read_text(encoding="utf-8")
    with respx.mock:
        mock()
        again = run(client, enrich_folder, tmp_path)
    assert (again.output_dir / "manifest.json").read_text(encoding="utf-8") == first


def test_no_output_file_holds_the_contact_address(result):
    for path in result.output_dir.iterdir():
        assert "example.invalid" not in path.read_text(encoding="utf-8")


# --- input checks -------------------------------------------------------------------------------


def test_a_changed_input_file_is_refused_before_any_request(client, enrich_folder, tmp_path):
    path = enrich_folder / "listing_facts.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with respx.mock:
        route = respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({}))
        with pytest.raises(PipelineError, match="listing_facts.jsonl"):
            run(client, enrich_folder, tmp_path)
        assert not route.called


def test_a_missing_manifest_is_a_clear_error(client, enrich_folder, tmp_path):
    (enrich_folder / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="manifest"):
        run(client, enrich_folder, tmp_path)


def test_a_damaged_manifest_is_a_clear_error(client, enrich_folder, tmp_path):
    (enrich_folder / "manifest.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(PipelineError, match="manifest"):
        run(client, enrich_folder, tmp_path)


def test_an_enrich_output_for_another_country_filter_is_refused(client, enrich_folder, tmp_path):
    with pytest.raises(PipelineError, match="country"):
        run_wikipedia_summary(
            client,
            enrich_folder=enrich_folder,
            out_dir=tmp_path / "out",
            dump_date="20261101",
            today=TODAY,
            country_iso=None,
            rules=RULES,
        )


def test_a_damaged_row_is_a_clear_error_without_quoting_it(client, tmp_path):
    folder = write_enrich_output(tmp_path / "bad", destinations=[], listings=[])
    path = folder / "destination_facts.jsonl"
    path.write_text('{"secret": 1}\n', encoding="utf-8")
    import hashlib

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["destination_facts.jsonl"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError) as raised:
        run(client, folder, tmp_path)
    assert "secret" not in str(raised.value)


def test_a_bad_dump_date_is_a_clear_error(client, enrich_folder, tmp_path):
    with pytest.raises(PipelineError, match="YYYYMMDD"):
        run_wikipedia_summary(
            client,
            enrich_folder=enrich_folder,
            out_dir=tmp_path / "out",
            dump_date="2026-11-01",
            today=TODAY,
            country_iso="IN",
            rules=RULES,
        )
