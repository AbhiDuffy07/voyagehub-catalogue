"""A small synthetic Wikivoyage world shared by the discover tests."""

from tests.apifakes import item_claim, string_claim
from tests.dumpfixture import page

PAGES = [
    page("Asia", "{{usablecontinent}}", page_id=2),
    page("India", "{{IsPartOf|Asia}}{{usablecountry}}", page_id=1),
    page("Nepal", "{{IsPartOf|Asia}}{{usablecountry}}", page_id=3),
    page("Rajasthan", "{{IsPartOf|India}}{{usableregion}}", page_id=4),
    page("Jaipur", "{{IsPartOf|Rajasthan}}{{guidecity}}", page_id=5, revision=905),
    page("Agra", "{{IsPartOf|India}}{{usablecity}}", page_id=6),
    page("Pokhara", "{{IsPartOf|Nepal}}{{usablecity}}", page_id=7),
    page("Outlineville", "{{IsPartOf|India}}{{outlinecity}}", page_id=8),
    page("Europe", "{{usablecontinent}}", page_id=10),
    page("Gibraltar", "{{IsPartOf|Europe}}{{usablecity}}", page_id=9),
    page("Ranthambore", "{{IsPartOf|Rajasthan}}{{usablepark}}", page_id=11),
    page("Talsi", "{{IsPartOf|India}}{{usablecity}}{{outlinecity}}", page_id=12),
    page("Kustavi", "{{IsPartOf|Nepal|Elsewhere}}{{usablecity}}", page_id=13),
    page("Golden Triangle", "{{IsPartOf|India}}{{usableitinerary}}", page_id=14),
    page("Notes page", "just text, no templates", page_id=15),
    page("Old Jaipur", "", page_id=16, redirect="Jaipur"),
]
IDS = {
    "India": "Q668",
    "Nepal": "Q837",
    "Jaipur": "Q1",
    "Agra": "Q2",
    "Pokhara": "Q3",
    "Ranthambore": "Q4",
    "Gibraltar": "Q5",
}
ENTITIES = {
    "Q668": {"P297": [string_claim("IN")]},
    "Q837": {"P297": [string_claim("NP")]},
    "Q1": {"P17": [item_claim("Q668")]},
    "Q2": {"P17": [item_claim("Q668")]},
    "Q3": {"P17": [item_claim("Q668")]},  # Wikidata says India, the tree says Nepal
    "Q4": {},
    "Q5": {"P17": [item_claim("Q145")]},
    "Q145": {"P297": [string_claim("GB")]},
}
