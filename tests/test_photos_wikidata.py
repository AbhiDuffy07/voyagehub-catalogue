import httpx
import pytest
import respx

from catalogue.errors import PipelineError
from catalogue.photos.wikidata import fetch_media_claims
from tests.apifakes import fake_wikidata, string_claim

WD = "https://www.wikidata.org/w/api.php"


@respx.mock
def test_reads_p18_files_with_rank_and_the_commons_category(client):
    respx.get(WD).mock(
        side_effect=fake_wikidata(
            {
                "Q1": {
                    "P18": [string_claim("Fort.jpg")],
                    "P373": [string_claim("Amer Fort")],
                }
            }
        )
    )
    claims = fetch_media_claims(client, ["Q1"])["Q1"]
    assert claims.p18 == (("Fort.jpg", "normal"),)
    assert claims.commons_category == "Amer Fort"


@respx.mock
def test_several_p18_are_all_kept_with_their_ranks_and_none_is_chosen(client):
    respx.get(WD).mock(
        side_effect=fake_wikidata(
            {"Q1": {"P18": [string_claim("B.jpg"), string_claim("A.jpg"), string_claim("A.jpg")]}}
        )
    )
    claims = fetch_media_claims(client, ["Q1"])["Q1"]
    assert claims.p18 == (("B.jpg", "normal"), ("A.jpg", "normal"))


@respx.mock
def test_preferred_rank_wins_and_deprecated_is_ignored(client):
    respx.get(WD).mock(
        side_effect=fake_wikidata(
            {
                "Q1": {
                    "P18": [
                        string_claim("Old.jpg", rank="deprecated"),
                        string_claim("Best.jpg", rank="preferred"),
                        string_claim("Other.jpg"),
                    ]
                },
                "Q2": {"P18": [string_claim("Gone.jpg", rank="deprecated")]},
            }
        )
    )
    result = fetch_media_claims(client, ["Q1", "Q2"])
    assert result["Q1"].p18 == (("Best.jpg", "preferred"),)
    assert result["Q2"].p18 == ()


@respx.mock
def test_two_different_commons_categories_are_not_guessed(client):
    respx.get(WD).mock(
        side_effect=fake_wikidata({"Q1": {"P373": [string_claim("A"), string_claim("B")]}})
    )
    claims = fetch_media_claims(client, ["Q1"])["Q1"]
    assert claims.commons_category is None
    assert claims.category_conflict is True


@respx.mock
def test_no_commons_category_is_not_a_conflict(client):
    respx.get(WD).mock(side_effect=fake_wikidata({"Q1": {}}))
    assert fetch_media_claims(client, ["Q1"])["Q1"].category_conflict is False


@respx.mock
def test_items_are_batched_by_50_and_malformed_ids_are_skipped(client):
    seen = []
    qids = [f"Q{n}" for n in range(1, 121)]
    respx.get(WD).mock(side_effect=fake_wikidata({}, seen=seen))
    result = fetch_media_claims(client, qids + ["bad", "Q1"])
    assert [len(b) for b in seen] == [50, 50, 20]
    assert result == {}  # missing items have no claims


@respx.mock
def test_a_merged_item_is_found_under_the_id_that_was_asked_for(client):
    def answer(request):
        body = {
            "Q1": {"type": "item", "id": "Q9", "claims": {"P18": [string_claim("New.jpg")]}},
        }
        return httpx.Response(200, json={"entities": body, "success": 1})

    respx.get(WD).mock(side_effect=answer)
    assert fetch_media_claims(client, ["Q1"])["Q1"].p18 == (("New.jpg", "normal"),)


@respx.mock
def test_a_reply_that_leaves_out_an_item_stops_the_run(client):
    respx.get(WD).mock(
        return_value=httpx.Response(200, json={"entities": {"Q1": {"id": "Q1"}}, "success": 1})
    )
    with pytest.raises(PipelineError, match="every requested item"):
        fetch_media_claims(client, ["Q1", "Q2"])
