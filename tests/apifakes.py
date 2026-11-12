"""Fake Wikivoyage and Wikidata API replies for tests (shapes checked against the real APIs)."""

import httpx


def item_claim(qid, rank="normal"):
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "wikibase-entityid",
                "value": {"entity-type": "item", "id": qid},
            },
        },
    }


def string_claim(value, rank="normal"):
    return {
        "rank": rank,
        "mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": value}},
    }


def fake_wikivoyage(ids: dict[str, str], seen: list[list[str]] | None = None):
    """Answer a pageprops query from a title -> Q-ID table. Unknown titles come back missing."""

    def answer(request: httpx.Request) -> httpx.Response:
        titles = request.url.params["titles"].split("|")
        if seen is not None:
            seen.append(titles)
        pages = []
        for title in titles:
            if title in ids:
                pages.append({"ns": 0, "title": title, "pageprops": {"wikibase_item": ids[title]}})
            else:
                pages.append({"ns": 0, "title": title, "missing": True})
        # The real API does not keep the request order, so neither does this fake.
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": pages[::-1]}})

    return answer


def fake_wikidata(entities: dict[str, dict], seen: list[list[str]] | None = None):
    """Answer wbgetentities from a Q-ID -> claims table. Unknown IDs come back missing."""

    def answer(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params["ids"].split("|")
        if seen is not None:
            seen.append(wanted)
        body = {}
        for qid in wanted:
            if qid in entities:
                body[qid] = {"type": "item", "id": qid, "claims": entities[qid]}
            else:
                body[qid] = {"id": qid, "missing": ""}
        return httpx.Response(200, json={"entities": body, "success": 1})

    return answer


EARTH = "http://www.wikidata.org/entity/Q2"
MOON = "http://www.wikidata.org/entity/Q405"


def coordinate_claim(lat, lon, *, globe=EARTH, rank="normal"):
    """A P625 statement, shaped like the real API (globe-coordinate value)."""
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "globecoordinate",
                "value": {
                    "latitude": lat,
                    "longitude": lon,
                    "altitude": None,
                    "precision": 1e-05,
                    "globe": globe,
                },
            },
        },
    }


def time_qualifier(date):
    """A time-valued qualifier snak, like `+2024-03-01T00:00:00Z`."""
    return {
        "snaktype": "value",
        "datavalue": {
            "type": "time",
            "value": {"time": date, "precision": 11, "calendarmodel": "x"},
        },
    }


def item_snak(qid):
    """An item-valued qualifier snak."""
    return {
        "snaktype": "value",
        "datavalue": {
            "type": "wikibase-entityid",
            "value": {"entity-type": "item", "id": qid},
        },
    }


def part_of_whs_claim(parent_qid=None, *, end=None, rank="normal"):
    """A P1435 statement "part of UNESCO World Heritage Site" (Q43113623). Wikidata records the
    parent site as a P361 qualifier on this statement (checked on Amber Fort, 2026-11-12)."""
    statement = item_claim("Q43113623", rank=rank)
    qualifiers = {}
    if parent_qid:
        qualifiers["P361"] = [item_snak(parent_qid)]
    if end:
        qualifiers["P582"] = [time_qualifier(end)]
    if qualifiers:
        statement["qualifiers"] = qualifiers
    return statement


def quantity_claim(amount, unit_qid=None, *, qualifiers=None, rank="normal"):
    """A P2555 (fee) statement. `unit_qid` None means a plain number (unit "1")."""
    unit = f"http://www.wikidata.org/entity/{unit_qid}" if unit_qid else "1"
    statement = {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "quantity", "value": {"amount": amount, "unit": unit}},
        },
    }
    if qualifiers:
        statement["qualifiers"] = {prop: [snak] for prop, snak in qualifiers.items()}
    return statement


def item_claim_with_end(qid, end="+2001-01-01T00:00:00Z"):
    """An item statement that has ended (qualifier P582)."""
    statement = item_claim(qid)
    statement["qualifiers"] = {"P582": [time_qualifier(end)]}
    return statement


def fake_wikipedia(pages, redirects=None, seen=None):
    """Answer an enwiki pageprops query.

    `pages` maps a canonical title to {"qid": ..., "disambiguation": bool}; a title not listed is
    missing. `redirects` maps a title to the canonical title it redirects to. Like the real API it
    normalises underscores and the first letter, reports `normalized` and `redirects` pairs, and
    returns pages in a different order from the request.
    """
    redirects = redirects or {}

    def answer(request: httpx.Request) -> httpx.Response:
        titles = request.url.params["titles"].split("|")
        if seen is not None:
            seen.append(titles)
        normalized, redirected, found = [], [], []
        for raw in titles:
            title = raw.replace("_", " ")
            title = title[:1].upper() + title[1:]
            if title != raw:
                normalized.append({"from": raw, "to": title})
            if title in redirects and request.url.params.get("redirects"):
                redirected.append({"from": title, "to": redirects[title]})
                title = redirects[title]
            if title in pages:
                props = {}
                if pages[title].get("qid"):
                    props["wikibase_item"] = pages[title]["qid"]
                if pages[title].get("disambiguation"):
                    props["disambiguation"] = ""
                page = {"pageid": 1, "ns": 0, "title": title}
                if props:
                    page["pageprops"] = props
            else:
                page = {"ns": 0, "title": title, "missing": True}
            if page not in found:
                found.append(page)
        query = {"pages": found[::-1]}
        if normalized:
            query["normalized"] = normalized
        if redirected:
            query["redirects"] = redirected
        return httpx.Response(200, json={"batchcomplete": True, "query": query})

    return answer


def fake_wikidata_full(entities, redirects=None, seen=None):
    """Answer wbgetentities with whole entity bodies (see `entity`). Unknown IDs are missing; an ID
    in `redirects` comes back as the target's body, with a `redirects` note, like the real API."""
    redirects = redirects or {}

    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        body = {}
        for qid in request.url.params["ids"].split("|"):
            target = redirects.get(qid, qid)
            if target in entities:
                item = {**entities[target], "id": target}
                if qid in redirects:
                    item["redirects"] = {"from": qid, "to": target}
                body[qid] = item
            else:
                body[qid] = {"id": qid, "missing": ""}
        return httpx.Response(200, json={"entities": body, "success": 1})

    return answer


def entity(claims, *, revid=1, label=None, description=None, enwiki=None):
    """A full wbgetentities entity body for the B4 parser."""
    body = {"type": "item", "id": "Q0", "lastrevid": revid, "claims": claims}
    if label:
        body["labels"] = {"en": {"language": "en", "value": label}}
    if description:
        body["descriptions"] = {"en": {"language": "en", "value": description}}
    if enwiki:
        body["sitelinks"] = {"enwiki": {"site": "enwiki", "title": enwiki, "badges": []}}
    return body


def fake_wikipedia_summaries(pages, redirects=None, seen=None, split=None, omit=()):
    """Answer an enwiki `prop=extracts|revisions|pageprops` query (formatversion 2).

    `pages` maps a canonical title to {"extract": str | None, "revid": int, "timestamp": str,
    "disambiguation": bool}; a title not listed is missing. `redirects` maps a title to the title
    it redirects to. `split=N` makes the reply carry only N pages and a `continue` block, the rest
    following when the request passes `excontinue`. `omit` lists titles the reply leaves out
    (a truncated reply). Like the real API, it normalises underscores and the first letter and
    returns pages in a different order from the request.
    """
    redirects = redirects or {}

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if seen is not None:
            seen.append(dict(params))
        normalized, redirected, found = [], [], []
        for raw in params["titles"].split("|"):
            title = raw.replace("_", " ")
            title = title[:1].upper() + title[1:]
            if title != raw:
                normalized.append({"from": raw, "to": title})
            if title in redirects and params.get("redirects"):
                redirected.append({"from": title, "to": redirects[title]})
                title = redirects[title]
            if title in omit:
                continue
            if title in pages:
                data = pages[title]
                page = {"pageid": 1, "ns": 0, "title": title}
                if data.get("extract") is not None:
                    page["extract"] = data["extract"]
                page["revisions"] = [
                    {"revid": data["revid"], "parentid": 0, "timestamp": data["timestamp"]}
                ]
                if data.get("disambiguation"):
                    page["pageprops"] = {"disambiguation": ""}
            else:
                page = {"ns": 0, "title": title, "missing": True}
            if page not in found:
                found.append(page)
        query = {"pages": found[::-1]}
        body = {"batchcomplete": True, "query": query}
        offset = int(params.get("excontinue", 0))
        if split is not None:
            if offset == 0:
                query["pages"] = found[:split]
                body = {"continue": {"excontinue": split, "continue": "||"}, "query": query}
            else:
                query["pages"] = found[offset:]
        if offset == 0:
            if normalized:
                query["normalized"] = normalized
            if redirected:
                query["redirects"] = redirected
        return httpx.Response(200, json=body)

    return answer
