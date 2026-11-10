"""Dependent territories that have their own ISO 3166-1 code count as their own country.

Wikivoyage does not give every territory a country article: Puerto Rico is a region, Gibraltar
is a city. The tree then ends at a continent with no country (`no_country_above`). For those pages
this step looks along the chain, nearest page first, for one whose Wikidata item has exactly one
ISO 3166-1 code (P297). That page becomes the catalogue country, with that code. Only countries
and territories carry P297, so a region or a city never qualifies by accident.

If no page in the chain has a code the page stays unassigned. Nothing is guessed.
"""

from catalogue.discover.confirm import CountryInfo, country_info, fetch_entities, fetch_wikidata_ids
from catalogue.discover.tree import Assignment
from catalogue.http import PoliteClient

STUCK = "no_country_above"


def resolve_territories(
    client: PoliteClient,
    assignments: dict[str, Assignment],
    countries: dict[str, CountryInfo],
) -> tuple[dict[str, Assignment], dict[str, CountryInfo]]:
    """Return updated copies of the assignments and the country table."""
    stuck = {title: a for title, a in assignments.items() if a.problem == STUCK}
    if not stuck:
        return assignments, countries

    chains = {title: (title, *a.ancestors) for title, a in stuck.items()}
    wanted = [page for chain in chains.values() for page in chain]
    ids = fetch_wikidata_ids(client, wanted)
    entities = fetch_entities(client, ids.values())

    new_assignments = dict(assignments)
    new_countries = dict(countries)
    for title, chain in chains.items():
        for index, page in enumerate(chain):
            qid = ids.get(page)
            facts = entities.get(qid) if qid else None
            if facts is not None and len(set(facts.iso_codes)) == 1:
                new_countries.setdefault(page, country_info(qid, facts))
                new_assignments[title] = Assignment(page, chain[1 : index + 1], None)
                break
    return new_assignments, new_countries
