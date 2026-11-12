import json

import pytest

from catalogue.enrich.inputs import (
    iter_listings,
    read_articles,
    read_destination_states,
    read_page_geo,
    verify_inputs,
)
from catalogue.errors import PipelineError
from tests.enrichfixture import article, listing, write_inputs

ARTICLES = [
    article(1, "Jaipur", qid="Q66485"),
    article(2, "Abhaneri", role="destination_outline", status="outline"),
]
LISTINGS = [listing(1, 1, "Amber Fort", wikidata="Q1"), listing(1, 2, "City Palace")]
DESTINATIONS = [{"wikivoyage_page_id": 1, "confirmation": "confirmed", "confirmation_reason": None}]


@pytest.fixture
def folders(tmp_path):
    return write_inputs(
        tmp_path,
        articles=ARTICLES,
        listings=LISTINGS,
        destinations=DESTINATIONS,
        page_geo={2: ("", "")},
    )


def test_verified_inputs_are_read(folders):
    discover, listings = folders
    verify_inputs(discover, listings, country_filter="IN")
    articles = read_articles(discover)
    assert [a.page_id for a in articles] == [1, 2]
    assert articles[0].wikidata_id == "Q66485" and articles[1].wikidata_id is None
    assert articles[1].role == "destination_outline" and articles[0].tree_country_iso == "IN"
    assert read_destination_states(discover) == {1: ("confirmed", None)}
    assert read_page_geo(listings) == {1: ("26.92", "75.82"), 2: ("", "")}
    rows = list(iter_listings(listings))
    assert [(r.page_id, r.ordinal, r.name) for r in rows] == [
        (1, 1, "Amber Fort"),
        (1, 2, "City Palace"),
    ]
    assert rows[0].wikidata == "Q1" and rows[0].page_lat == "26.92" and not rows[0].from_marker


def test_the_first_geo_of_a_page_is_the_one_used(tmp_path):
    discover, listings = write_inputs(tmp_path, articles=ARTICLES[:1], listings=[])
    path = listings / "sections.jsonl"
    extra = {"page_id": 1, "ordinal": 1, "page_lat": "9", "page_long": "9"}
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(extra) + "\n", encoding="utf-8")
    assert read_page_geo(listings)[1] == ("26.92", "75.82")


def test_changed_articles_are_refused(folders):
    discover, listings = folders
    path = discover / "articles.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="articles"):
        verify_inputs(discover, listings, country_filter="IN")


@pytest.mark.parametrize("name", ["listings.jsonl", "sections.jsonl"])
def test_changed_listing_outputs_are_refused(folders, name):
    discover, listings = folders
    path = listings / name
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match=name):
        verify_inputs(discover, listings, country_filter="IN")


def test_a_run_for_another_country_is_refused(folders):
    discover, listings = folders
    with pytest.raises(PipelineError, match="country"):
        verify_inputs(discover, listings, country_filter=None)


def test_a_missing_manifest_or_file_is_a_clear_error(folders):
    discover, listings = folders
    (listings / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="manifest"):
        verify_inputs(discover, listings, country_filter="IN")
    (discover / "articles.jsonl").unlink()
    with pytest.raises(PipelineError, match="articles"):
        read_articles(discover)


def test_a_damaged_manifest_is_a_clear_error(folders):
    discover, listings = folders
    (listings / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PipelineError, match="manifest"):
        verify_inputs(discover, listings, country_filter="IN")


def test_a_damaged_row_is_a_clear_error_without_quoting_it(folders):
    discover, listings = folders
    (listings / "listings.jsonl").write_text('{"secret": 1}\n', encoding="utf-8")
    with pytest.raises(PipelineError) as raised:
        list(iter_listings(listings))
    assert "secret" not in str(raised.value)
