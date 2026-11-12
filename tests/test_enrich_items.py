import httpx
import pytest
import respx

from catalogue.enrich.items import fetch_items
from catalogue.errors import PipelineError
from tests.apifakes import coordinate_claim, entity, fake_wikidata_full

WIKIDATA = "https://www.wikidata.org/w/api.php"


@respx.mock
def test_fetches_and_parses_items(client):
    respx.get(WIKIDATA).mock(
        side_effect=fake_wikidata_full(
            {
                "Q1": entity(
                    {"P625": [coordinate_claim(26.9, 75.8)]}, label="Jaipur", enwiki="Jaipur"
                )
            }
        )
    )
    result = fetch_items(client, ["Q1"])
    item = result.items["Q1"]
    assert item.label == "Jaipur" and item.enwiki_title == "Jaipur"
    assert item.coordinate.lat == 26.9
    assert result.missing == frozenset() and result.redirects == {}


@respx.mock
def test_asks_only_for_what_it_needs(client):
    seen = []
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({"Q1": entity({})}, seen=seen))
    fetch_items(client, ["Q1"])
    params = seen[0]
    assert params["action"] == "wbgetentities"
    assert set(params["props"].split("|")) == {"claims", "sitelinks", "labels", "descriptions"}
    assert params["languages"] == "en" and params["sitefilter"] == "enwiki"


@respx.mock
def test_batches_of_fifty_sorted_without_duplicates_and_bad_ids(client):
    seen = []
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({}, seen=seen))
    ids = [f"Q{n}" for n in range(1, 121)] + ["Q5", "q7", "Q0", "Qx", "", "P31", "Q1; drop"]
    fetch_items(client, ids)
    batches = [p["ids"].split("|") for p in seen]
    assert [len(b) for b in batches] == [50, 50, 20]
    flat = [q for b in batches for q in b]
    assert flat == sorted(set(flat), key=lambda q: int(q[1:]))
    assert len(flat) == 120


@respx.mock
def test_missing_items_are_reported_not_dropped_silently(client):
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({"Q1": entity({})}))
    result = fetch_items(client, ["Q1", "Q2"])
    assert set(result.items) == {"Q1"}
    assert result.missing == frozenset({"Q2"})


@respx.mock
def test_a_merged_item_is_reported_and_its_facts_are_not_attached_to_the_old_id(client):
    respx.get(WIKIDATA).mock(
        side_effect=fake_wikidata_full(
            {"Q9": entity({"P625": [coordinate_claim(1.0, 2.0)]}, label="New")},
            redirects={"Q1": "Q9"},
        )
    )
    result = fetch_items(client, ["Q1"])
    assert result.redirects == {"Q1": "Q9"}
    assert "Q1" not in result.items
    assert result.items["Q9"].label == "New"


@respx.mock
def test_no_ids_means_no_requests(client):
    route = respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({}))
    result = fetch_items(client, [])
    assert not route.called and result.items == {}


@respx.mock
def test_a_reply_that_leaves_out_a_requested_item_is_an_error_not_a_flag(client):
    def short(request):
        return httpx.Response(200, json={"entities": {"Q1": {"id": "Q1", "claims": {}}}})

    respx.get(WIKIDATA).mock(side_effect=short)
    with pytest.raises(PipelineError, match="every requested"):
        fetch_items(client, ["Q1", "Q2"])


@respx.mock
def test_a_reply_without_entities_is_an_error(client):
    respx.get(WIKIDATA).mock(return_value=httpx.Response(200, json={"success": 1}))
    with pytest.raises(PipelineError, match="every requested"):
        fetch_items(client, ["Q1"])
