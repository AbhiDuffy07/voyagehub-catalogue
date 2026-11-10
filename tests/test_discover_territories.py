import pytest
import respx

from catalogue.discover.confirm import (
    CountryInfo,
    confirm_destinations,
    resolve_countries,
)
from catalogue.discover.parse import Article, ParsedDump
from catalogue.discover.territories import resolve_territories
from catalogue.discover.tree import assign_countries
from tests.apifakes import fake_wikidata, fake_wikivoyage, item_claim, string_claim

WV = "https://en.wikivoyage.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"


def article(title, kind, parent=None):
    return Article(
        page_id=abs(hash(title)) % 100_000,
        title=title,
        revision_id=1,
        timestamp="t",
        type=kind,
        status="usable",
        parent=parent,
    )


ARTICLES = [
    article("Oceania", "continent"),
    article("Europe", "continent"),
    article("North America", "continent"),
    article("Guam", "country", "Oceania"),
    article("Tumon", "city", "Guam"),
    article("Russia", "country", "Europe"),
    article("Sevastopol", "city", "Russia"),
    article("Georgia", "country", "Europe"),
    article("Abkhazia", "country", "Europe"),
    article("Sukhumi", "city", "Abkhazia"),
    article("Netherlands", "country", "Europe"),
    article("Amsterdam", "city", "Netherlands"),
    article("Gibraltar", "city", "Europe"),
    article("Caribbean", "region", "North America"),
    article("Puerto Rico", "region", "Caribbean"),
    article("Aguadilla", "city", "Puerto Rico"),
    article("Wasteland", "region", "Europe"),
    article("Remote", "city", "Wasteland"),
    article("Lost", "city", "Nowhere"),
]
IDS = {
    "Guam": "Q16635",
    "Tumon": "Q6120935",
    "Russia": "Q159",
    "Sevastopol": "Q7525",
    "Georgia": "Q230",
    "Abkhazia": "Q23334",
    "Sukhumi": "Q1",
    "Netherlands": "Q55",
    "Amsterdam": "Q727",
    "Gibraltar": "Q1410",
    "Puerto Rico": "Q1183",
    "Aguadilla": "Q2",
    "Remote": "Q3",
    "Wasteland": "Q4",
}
ENTITIES = {
    "Q16635": {"P297": [string_claim("GU")], "P17": [item_claim("Q30")]},
    "Q6120935": {"P17": [item_claim("Q30")]},
    "Q159": {"P297": [string_claim("RU")], "P17": [item_claim("Q159")]},
    "Q7525": {"P17": [item_claim("Q212")]},  # Ukraine: disputed, must stay a conflict
    "Q230": {"P297": [string_claim("GE")], "P17": [item_claim("Q230")]},
    "Q23334": {"P17": [item_claim("Q230")]},  # no ISO code of its own
    "Q1": {"P17": [item_claim("Q230")]},
    "Q55": {"P297": [string_claim("NL", "deprecated")], "P17": [item_claim("Q55")]},
    "Q727": {"P17": [item_claim("Q55")]},
    "Q1410": {"P297": [string_claim("GI")], "P17": [item_claim("Q145")]},
    "Q1183": {"P297": [string_claim("PR")], "P17": [item_claim("Q30")]},
    "Q2": {"P17": [item_claim("Q30")]},
    "Q3": {},
    "Q4": {},
    "Q30": {"P297": [string_claim("US")]},
    "Q145": {"P297": [string_claim("GB")]},
    "Q212": {"P297": [string_claim("UA")]},
}


@pytest.fixture
def world(client):
    parsed = ParsedDump({a.title: a for a in ARTICLES}, {})
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(ENTITIES))
        countries = resolve_countries(client, parsed, overrides={"Netherlands": "NL"})
        assignments, countries = resolve_territories(client, assign_countries(parsed), countries)
        titles = ["Tumon", "Sevastopol", "Sukhumi", "Amsterdam", "Gibraltar", "Aguadilla"]
        confirmations = confirm_destinations(client, titles, assignments, countries)
        yield parsed, assignments, countries, confirmations


def test_an_override_beats_wikidata_and_without_it_the_code_stays_empty(client):
    parsed = ParsedDump({a.title: a for a in ARTICLES}, {})
    with respx.mock:
        respx.get(WV).mock(side_effect=fake_wikivoyage(IDS))
        respx.get(WD).mock(side_effect=fake_wikidata(ENTITIES))
        plain = resolve_countries(client, parsed)
        with_override = resolve_countries(client, parsed, overrides={"Netherlands": "NL"})

    assert plain["Netherlands"].iso is None
    assert with_override["Netherlands"].iso == "NL"


def test_a_dependent_territory_with_its_own_code_is_its_own_country(world):
    _, _, _, confirmations = world

    tumon = confirmations["Tumon"]
    assert (tumon.state, tumon.country_iso) == ("confirmed", "GU")


def test_a_disputed_place_stays_a_conflict_with_no_country(world):
    _, _, _, confirmations = world

    sevastopol = confirmations["Sevastopol"]
    assert (sevastopol.state, sevastopol.country_iso) == ("conflict", None)
    assert sevastopol.wikidata_country_iso == "UA"


def test_the_sovereign_state_rule_needs_the_territory_to_have_its_own_code(world):
    _, _, _, confirmations = world

    assert confirmations["Sukhumi"].state == "conflict"


def test_the_override_flows_through_to_the_confirmed_rows(world):
    _, _, _, confirmations = world

    assert (confirmations["Amsterdam"].state, confirmations["Amsterdam"].country_iso) == (
        "confirmed",
        "NL",
    )


def test_a_page_that_has_its_own_iso_code_becomes_a_country(world):
    _, assignments, countries, confirmations = world

    assert assignments["Gibraltar"].country == "Gibraltar"
    assert countries["Gibraltar"] == CountryInfo(
        qid="Q1410", iso="GI", parent_states=frozenset({"Q145"})
    )
    assert (confirmations["Gibraltar"].state, confirmations["Gibraltar"].country_iso) == (
        "confirmed",
        "GI",
    )


def test_a_page_below_a_territory_is_assigned_to_the_nearest_one(world):
    _, assignments, _, confirmations = world

    assert assignments["Aguadilla"].country == "Puerto Rico"
    assert assignments["Aguadilla"].ancestors == ("Puerto Rico",)
    assert confirmations["Aguadilla"].country_iso == "PR"


def test_pages_with_no_iso_code_anywhere_in_their_chain_stay_unassigned(world):
    _, assignments, _, _ = world

    assert assignments["Remote"].problem == "no_country_above"
    assert assignments["Remote"].country is None


def test_other_problems_are_left_alone(world):
    _, assignments, _, _ = world

    assert assignments["Lost"].problem == "parent_missing"


def test_normal_country_assignments_are_not_touched(world):
    _, assignments, _, _ = world

    assert assignments["Amsterdam"].country == "Netherlands"
    assert assignments["Tumon"].ancestors == ("Guam",)
