import httpx
import pytest
import respx

from catalogue.enrich.links import parse_wikipedia_value, resolve_wikipedia_links
from tests.apifakes import fake_wikipedia

ENWIKI = "https://en.wikipedia.org/w/api.php"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Amer Fort", ("title", "Amer Fort")),
        ("  Amer_Fort ", ("title", "Amer Fort")),
        ("en:Amer Fort", ("title", "Amer Fort")),
        ("EN:Amer Fort", ("title", "Amer Fort")),
        (":Amer Fort", ("title", "Amer Fort")),
        ("https://en.wikipedia.org/wiki/Amer_Fort", ("title", "Amer Fort")),
        ("http://en.wikipedia.org/wiki/Caf%C3%A9_Royal", ("title", "Café Royal")),
        ("de:Schloss Amber", ("other_language", None)),
        ("https://de.wikipedia.org/wiki/Schloss", ("other_language", None)),
        ("Amer Fort#History", ("section_link", None)),
        ("", ("bad_value", None)),
        ("   ", ("bad_value", None)),
        ("A|B", ("bad_value", None)),
        ("[[Amer Fort]]", ("bad_value", None)),
        ("https://example.com/wiki/Amer_Fort", ("bad_value", None)),
        ("x" * 300, ("bad_value", None)),
        ("Star Trek: Voyager", ("title", "Star Trek: Voyager")),
        ("Category:Forts", ("title", "Category:Forts")),
    ],
)
def test_parse_wikipedia_value(value, expected):
    assert parse_wikipedia_value(value) == expected


@respx.mock
def test_resolves_plain_redirected_missing_disambiguation_and_no_item(client):
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia(
            {
                "Amer Fort": {"qid": "Q1"},
                "Jaipur City Palace": {"qid": "Q2"},
                "Mercury": {"qid": "Q3", "disambiguation": True},
                "Bare Page": {},
            },
            redirects={"Amber Fort": "Amer Fort"},
        )
    )
    values = ["Amer_Fort", "Amber Fort", "Nowhere At All", "Mercury", "Bare Page"]
    got = resolve_wikipedia_links(client, values)
    assert (got["Amer_Fort"].status, got["Amer_Fort"].qid) == ("resolved", "Q1")
    assert (got["Amber Fort"].status, got["Amber Fort"].qid) == ("resolved_via_redirect", "Q1")
    assert (got["Nowhere At All"].status, got["Nowhere At All"].qid) == ("missing", None)
    assert (got["Mercury"].status, got["Mercury"].qid) == ("disambiguation", None)
    assert (got["Bare Page"].status, got["Bare Page"].qid) == ("no_item", None)


@respx.mock
def test_unresolvable_forms_are_classified_without_any_request(client):
    route = respx.get(ENWIKI).mock(side_effect=fake_wikipedia({}))
    got = resolve_wikipedia_links(client, ["de:Schloss", "Fort#History", "", "A|B"])
    assert not route.called
    assert {v: r.status for v, r in got.items()} == {
        "de:Schloss": "other_language",
        "Fort#History": "section_link",
        "": "bad_value",
        "A|B": "bad_value",
    }


@respx.mock
def test_titles_are_sent_in_batches_of_fifty_without_duplicates(client):
    seen: list[list[str]] = []
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia({}, seen=seen))
    values = [f"Place {n}" for n in range(120)] + ["Place 0", "place_0"]
    resolve_wikipedia_links(client, values)
    assert [len(batch) for batch in seen] == [50, 50, 20]
    assert len({t for batch in seen for t in batch}) == 120


@respx.mock
def test_two_spellings_of_one_title_both_resolve(client):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia({"Amer Fort": {"qid": "Q1"}}))
    got = resolve_wikipedia_links(client, ["Amer_Fort", "amer Fort", "en:Amer Fort"])
    assert {r.qid for r in got.values()} == {"Q1"}
    assert all(r.status == "resolved" for r in got.values())


@respx.mock
def test_a_reply_missing_a_requested_title_is_marked_not_guessed(client):
    def short_answer(request):
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": []}})

    respx.get(ENWIKI).mock(side_effect=short_answer)
    got = resolve_wikipedia_links(client, ["Amer Fort"])
    assert (got["Amer Fort"].status, got["Amer Fort"].qid) == ("no_answer", None)


@respx.mock
def test_a_redirect_to_a_section_is_not_resolved(client):
    def answer(request):
        return httpx.Response(
            200,
            json={
                "query": {
                    "redirects": [{"from": "Fort Gate", "to": "Amer Fort", "tofragment": "Gates"}],
                    "pages": [
                        {
                            "pageid": 1,
                            "ns": 0,
                            "title": "Amer Fort",
                            "pageprops": {"wikibase_item": "Q1"},
                        }
                    ],
                }
            },
        )

    respx.get(ENWIKI).mock(side_effect=answer)
    got = resolve_wikipedia_links(client, ["Fort Gate"])
    assert (got["Fort Gate"].status, got["Fort Gate"].qid) == ("section_link", None)


@respx.mock
def test_a_malformed_item_id_is_not_used(client):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia({"Amer Fort": {"qid": "not-a-qid"}}))
    got = resolve_wikipedia_links(client, ["Amer Fort"])
    assert (got["Amer Fort"].status, got["Amer Fort"].qid) == ("no_item", None)
