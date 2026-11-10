"""Confirm each destination's country with Wikidata (design: section 3, step 3).

The IsPartOf tree decides the country. Wikidata only confirms or contradicts it:
- `confirmed`: the page's Wikidata item (P17) names the same country as the tree.
- `conflict`: it names a different country. No country is chosen; the row is logged.
- `unconfirmed`: Wikidata cannot say (no item, or no P17). The tree's country is kept, labelled.
- `unassigned`: the tree could not place the page. Wikidata's country is shown as a hint only.

Wikivoyage `pageprops` gives each page's Wikidata ID (50 titles a request) and `wbgetentities`
gives the facts (50 items a request). No SPARQL: the query service is moving to a new backend.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from catalogue.discover.parse import ParsedDump
from catalogue.discover.tree import Assignment
from catalogue.http import PoliteClient

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BATCH_SIZE = 50  # the API limit for ordinary clients

COUNTRY = "P17"
ISO_CODE = "P297"
TIME_ZONE = "P421"


@dataclass(frozen=True, slots=True)
class EntityFacts:
    countries: frozenset[str]  # P17 values (Q-IDs)
    iso_codes: tuple[str, ...]  # P297 values
    time_zones: tuple[str, ...]  # P421 values (Q-IDs; their names are fetched later, in B4)


@dataclass(frozen=True, slots=True)
class CountryInfo:
    qid: str | None
    iso: str | None


@dataclass(frozen=True, slots=True)
class Confirmation:
    state: str  # confirmed, conflict, unconfirmed, unassigned
    reason: str | None
    wikidata_id: str | None
    country_iso: str | None  # the country to use; empty for conflict and unassigned
    wikidata_country_iso: str | None  # what Wikidata says, for logs and hints
    time_zone_qids: tuple[str, ...] = ()


def _batches(items: Iterable[str]) -> Iterator[list[str]]:
    unique = list(dict.fromkeys(items))
    for start in range(0, len(unique), BATCH_SIZE):
        yield unique[start : start + BATCH_SIZE]


def fetch_wikidata_ids(client: PoliteClient, titles: Sequence[str]) -> dict[str, str]:
    """Map Wikivoyage titles to Wikidata IDs. Pages without an item are left out."""
    found: dict[str, str] = {}
    for batch in _batches(titles):
        reply = client.get(
            WIKIVOYAGE_API,
            params={
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "titles": "|".join(batch),
                "format": "json",
                "formatversion": 2,
            },
        ).json()["query"]
        requested = {n["to"]: n["from"] for n in reply.get("normalized", [])}
        for page in reply["pages"]:
            qid = page.get("pageprops", {}).get("wikibase_item")
            title = requested.get(page["title"], page["title"])
            if qid and title in batch:
                found[title] = qid
    return found


def _usable(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop deprecated statements; if any are preferred, keep only those."""
    live = [s for s in statements if s.get("rank") != "deprecated"]
    preferred = [s for s in live if s.get("rank") == "preferred"]
    return preferred or live


def _values(claims: dict[str, Any], prop: str) -> list[Any]:
    values = []
    for statement in _usable(claims.get(prop, [])):
        snak = statement.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            value = snak["datavalue"]["value"]
            values.append(value["id"] if isinstance(value, dict) else value)
    return values


def fetch_entities(client: PoliteClient, qids: Iterable[str]) -> dict[str, EntityFacts]:
    """Fetch P17, P297 and P421 for Wikidata items. Missing items are left out."""
    facts: dict[str, EntityFacts] = {}
    for batch in _batches(qids):
        entities = client.get(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "claims",
                "format": "json",
            },
        ).json()["entities"]
        for qid, entity in entities.items():
            if "missing" in entity:
                continue
            claims = entity.get("claims", {})
            facts[qid] = EntityFacts(
                countries=frozenset(_values(claims, COUNTRY)),
                iso_codes=tuple(_values(claims, ISO_CODE)),
                time_zones=tuple(_values(claims, TIME_ZONE)),
            )
    return facts


def _single_iso(facts: EntityFacts | None) -> str | None:
    """An ISO code only when Wikidata gives exactly one. Two codes would be a guess."""
    return facts.iso_codes[0] if facts and len(set(facts.iso_codes)) == 1 else None


def resolve_countries(client: PoliteClient, parsed: ParsedDump) -> dict[str, CountryInfo]:
    """The Wikidata ID and ISO code of every country article."""
    titles = [t for t, a in parsed.articles.items() if a.type == "country"]
    ids = fetch_wikidata_ids(client, titles)
    entities = fetch_entities(client, ids.values())
    return {
        title: CountryInfo(qid=ids.get(title), iso=_single_iso(entities.get(ids.get(title, ""))))
        for title in titles
    }


def confirm_destinations(
    client: PoliteClient,
    titles: Sequence[str],
    assignments: dict[str, Assignment],
    countries: dict[str, CountryInfo],
) -> dict[str, Confirmation]:
    ids = fetch_wikidata_ids(client, titles)
    entities = fetch_entities(client, ids.values())

    # ISO codes for the countries Wikidata names, which may include ones the tree never used.
    known_iso = {info.qid: info.iso for info in countries.values() if info.qid}
    wanted = {q for e in entities.values() for q in e.countries} - set(known_iso)
    country_facts = fetch_entities(client, wanted)
    known_iso.update({qid: _single_iso(facts) for qid, facts in country_facts.items()})

    def wikidata_iso(facts: EntityFacts | None) -> str | None:
        codes = sorted({known_iso[q] for q in facts.countries if known_iso.get(q)}) if facts else []
        return "/".join(codes) or None

    results: dict[str, Confirmation] = {}
    for title in titles:
        assignment = assignments[title]
        qid = ids.get(title)
        facts = entities.get(qid) if qid else None
        hint = wikidata_iso(facts)
        zones = facts.time_zones if facts else ()

        if assignment.country is None:
            results[title] = Confirmation("unassigned", assignment.problem, qid, None, hint, zones)
            continue

        info = countries.get(assignment.country, CountryInfo(None, None))
        if qid is None:
            state, reason = "unconfirmed", "no_wikidata_id"
        elif facts is None:
            state, reason = "unconfirmed", "wikidata_item_missing"
        elif not facts.countries:
            state, reason = "unconfirmed", "no_p17"
        elif info.qid is None:
            state, reason = "unconfirmed", "country_has_no_wikidata_id"
        elif info.qid in facts.countries:
            state, reason = "confirmed", None
        else:
            state, reason = "conflict", "p17_disagrees"

        iso = None if state == "conflict" else info.iso
        results[title] = Confirmation(state, reason, qid, iso, hint, zones)
    return results
