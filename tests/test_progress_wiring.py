import respx

from catalogue.discover.confirm import fetch_entities, fetch_wikidata_ids
from catalogue.enrich.items import fetch_items
from catalogue.enrich.links import resolve_wikipedia_links
from catalogue.enrich.summary import fetch_summaries
from tests.apifakes import (
    entity,
    fake_wikidata,
    fake_wikidata_full,
    fake_wikipedia,
    fake_wikipedia_summaries,
    fake_wikivoyage,
    item_claim,
)

WIKIVOYAGE = "https://en.wikivoyage.org/w/api.php"
ENWIKI = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"


@respx.mock
def test_fetching_items_reports_items_done_of_distinct_ids(client, capsys):
    respx.get(WIKIDATA).mock(
        side_effect=fake_wikidata_full({q: entity({}) for q in ("Q1", "Q2", "Q3")})
    )
    fetch_items(client, ["Q1", "Q2", "Q3", "Q1"])
    out = capsys.readouterr().out
    assert "progress: Wikidata items 3 of 3 (100%) done" in out
    assert "Q1" not in out


@respx.mock
def test_fetching_nothing_reports_nothing(client, capsys):
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_full({}))
    fetch_items(client, [])
    assert "progress:" not in capsys.readouterr().out


@respx.mock
def test_looking_up_wikivoyage_titles_reports_titles_done(client, capsys):
    respx.get(WIKIVOYAGE).mock(side_effect=fake_wikivoyage({"Jaipur": "Q1", "Agra": "Q2"}))
    fetch_wikidata_ids(client, ["Jaipur", "Agra", "Jaipur"])
    out = capsys.readouterr().out
    assert "progress: Wikivoyage titles 2 of 2 (100%) done" in out
    assert "Jaipur" not in out


@respx.mock
def test_fetching_entities_reports_entities_done(client, capsys):
    respx.get(WIKIDATA).mock(
        side_effect=fake_wikidata({"Q1": {"P17": [item_claim("Q668")]}, "Q2": {}})
    )
    fetch_entities(client, ["Q1", "Q2"])
    assert "progress: Wikidata entities 2 of 2 (100%) done" in capsys.readouterr().out


@respx.mock
def test_resolving_links_counts_only_the_titles_it_looks_up(client, capsys):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia({"Amer Fort": {"qid": "Q1"}}))
    resolve_wikipedia_links(client, ["Amer Fort", "Hawa Mahal", "de:Schloss", ""])
    out = capsys.readouterr().out
    assert "progress: Wikipedia links 2 of 2 (100%) done" in out
    assert "Amer" not in out and "Schloss" not in out


@respx.mock
def test_fetching_summaries_reports_distinct_titles_done_and_no_text(client, capsys):
    page = {"extract": "Jaipur is a city.", "revid": 1, "timestamp": "2026-11-02T10:00:00Z"}
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({"Jaipur": page, "Agra": page}))
    fetch_summaries(client, ["Jaipur", "Agra", "Jaipur"])
    out = capsys.readouterr().out
    assert "progress: Wikipedia titles 2 of 2 (100%) done" in out
    assert "Jaipur" not in out and "city" not in out
