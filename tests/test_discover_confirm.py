import httpx
import pytest
import respx

from catalogue.discover.confirm import (
    EntityFacts,
    confirm_destinations,
    fetch_entities,
    fetch_wikidata_ids,
    resolve_countries,
)
from catalogue.discover.parse import Article, ParsedDump
from catalogue.discover.tree import assign_countries
from tests.apifakes import fake_wikidata, fake_wikivoyage, item_claim, string_claim

WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"


@respx.mock
def test_page_ids_are_requested_fifty_titles_at_a_time(client):
    titles = [f"Town {n}" for n in range(120)]
    seen: list[list[str]] = []
    respx.get(WV).mock(
        side_effect=fake_wikivoyage({t: f"Q{n}" for n, t in enumerate(titles)}, seen)
    )

    result = fetch_wikidata_ids(client, titles)

    assert [len(batch) for batch in seen] == [50, 50, 20]
    assert result["Town 7"] == "Q7"
    assert len(result) == 120


@respx.mock
def test_pages_without_a_wikidata_item_are_left_out(client):
    respx.get(WV).mock(side_effect=fake_wikivoyage({"Jaipur": "Q66485"}))

    assert fetch_wikidata_ids(client, ["Jaipur", "No item"]) == {"Jaipur": "Q66485"}


@respx.mock
def test_a_normalized_title_in_the_reply_is_matched_back_to_the_request(client):
    def answer(request):
        body = {
            "query": {
                "normalized": [{"from": "jaipur x", "to": "Jaipur x"}],
                "pages": [{"title": "Jaipur x", "pageprops": {"wikibase_item": "Q1"}}],
            }
        }
        return httpx.Response(200, json=body)

    respx.get(WV).mock(side_effect=answer)

    assert fetch_wikidata_ids(client, ["jaipur x"]) == {"jaipur x": "Q1"}


@respx.mock
def test_entity_facts_come_from_p17_p297_and_p421(client):
    claims = {
        "P17": [item_claim("Q668")],
        "P297": [string_claim("IN")],
        "P421": [item_claim("Q2"), item_claim("Q3")],
    }
    respx.get(WD).mock(side_effect=fake_wikidata({"Q1": claims}))

    facts = fetch_entities(client, ["Q1"])["Q1"]

    assert facts == EntityFacts(
        countries=frozenset({"Q668"}), iso_codes=("IN",), time_zones=("Q2", "Q3")
    )


@respx.mock
def test_a_deprecated_statement_is_ignored(client):
    claims = {"P17": [item_claim("Q1", "deprecated"), item_claim("Q2")]}
    respx.get(WD).mock(side_effect=fake_wikidata({"Q9": claims}))

    assert fetch_entities(client, ["Q9"])["Q9"].countries == frozenset({"Q2"})


@respx.mock
def test_preferred_statements_beat_normal_ones(client):
    claims = {"P17": [item_claim("Q1"), item_claim("Q2", "preferred")]}
    respx.get(WD).mock(side_effect=fake_wikidata({"Q9": claims}))

    assert fetch_entities(client, ["Q9"])["Q9"].countries == frozenset({"Q2"})


@respx.mock
def test_a_missing_entity_is_left_out_and_ids_are_batched(client):
    seen: list[list[str]] = []
    entities = {f"Q{n}": {} for n in range(1, 51)}
    respx.get(WD).mock(side_effect=fake_wikidata(entities, seen))

    result = fetch_entities(client, [f"Q{n}" for n in range(1, 52)])

    assert [len(batch) for batch in seen] == [50, 1]
    assert "Q51" not in result
    assert "Q50" in result


def article(title, kind, parent=None):
    return Article(
        page_id=abs(hash(title)) % 10_000,
        title=title,
        revision_id=1,
        timestamp="t",
        type=kind,
        status="usable",
        parent=parent,
    )


def build_world():
    parsed = ParsedDump(
        {
            a.title: a
            for a in [
                article("India", "country"),
                article("Nepal", "country"),
                article("Rajasthan", "region", "India"),
                article("Jaipur", "city", "Rajasthan"),
                article("Pokhara", "city", "Nepal"),
                article("Mystery", "city", "Nepal"),
                article("Bare", "city", "India"),
                article("Gone", "city", "India"),
                article("Gibraltar", "city", "Europe"),
                article("Europe", "continent"),
            ]
        },
        {},
    )
    return parsed, assign_countries(parsed)


WORLD_IDS = {
    "India": "Q668",
    "Nepal": "Q837",
    "Jaipur": "Q66485",
    "Pokhara": "Q1",  # Wikidata says India: a disagreement
    "Mystery": "Q2",  # the item exists but has no country statement
    "Bare": "Q3",  # also no country statement
    "Gibraltar": "Q1410",
}
WORLD_ENTITIES = {
    "Q668": {"P297": [string_claim("IN")]},
    "Q837": {"P297": [string_claim("NP")]},
    "Q66485": {"P17": [item_claim("Q668")]},
    "Q1": {"P17": [item_claim("Q668")]},
    "Q2": {},
    "Q3": {},
    "Q1410": {"P17": [item_claim("Q145")]},
    "Q145": {"P297": [string_claim("GB")]},
}


@pytest.fixture
def api():
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(WORLD_IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(WORLD_ENTITIES))
        yield


def test_country_articles_get_their_wikidata_id_and_iso_code(client, api):
    parsed, _ = build_world()

    countries = resolve_countries(client, parsed)

    assert (countries["India"].qid, countries["India"].iso) == ("Q668", "IN")
    assert (countries["Nepal"].qid, countries["Nepal"].iso) == ("Q837", "NP")


def run_confirm(client):
    parsed, assignments = build_world()
    countries = resolve_countries(client, parsed)
    titles = ["Jaipur", "Pokhara", "Mystery", "Bare", "Gone", "Gibraltar"]
    return confirm_destinations(client, titles, assignments, countries)


def test_agreeing_country_is_confirmed(client, api):
    result = run_confirm(client)["Jaipur"]

    assert (result.state, result.country_iso, result.wikidata_id) == ("confirmed", "IN", "Q66485")


def test_a_disagreement_is_a_conflict_with_no_country_chosen(client, api):
    result = run_confirm(client)["Pokhara"]

    assert (result.state, result.country_iso, result.reason) == ("conflict", None, "p17_disagrees")
    assert result.wikidata_country_iso == "IN"


def test_a_destination_with_no_country_statement_is_unconfirmed_but_keeps_the_tree_country(
    client, api
):
    result = run_confirm(client)["Mystery"]

    assert (result.state, result.country_iso, result.reason) == ("unconfirmed", "NP", "no_p17")


def test_an_entity_without_p17_is_unconfirmed(client, api):
    assert run_confirm(client)["Bare"].reason == "no_p17"


def test_a_page_without_a_wikidata_item_is_unconfirmed(client, api):
    result = run_confirm(client)["Gone"]

    assert (result.state, result.reason, result.country_iso) == (
        "unconfirmed",
        "no_wikidata_id",
        "IN",
    )


def test_a_page_the_tree_cannot_place_stays_unassigned_with_wikidata_only_as_a_hint(client, api):
    result = run_confirm(client)["Gibraltar"]

    assert (result.state, result.country_iso, result.reason) == (
        "unassigned",
        None,
        "no_country_above",
    )
    assert result.wikidata_country_iso == "GB"
