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


def string_claim(value):
    return {
        "rank": "normal",
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
