"""Fake Commons API replies for the photo tests (shapes checked against the real API)."""

import httpx

COMMONS = "https://commons.wikimedia.org/w/api.php"


def commons_file(
    *,
    width=4000,
    height=3000,
    mime="image/jpeg",
    licence="cc-by-sa-4.0",
    licence_name="CC BY-SA 4.0",
    licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
    artist='<a href="//commons.wikimedia.org/wiki/User:Ann">Ann &amp; Co</a>',
    marks=(),
    thumb=True,
    sha1="a" * 40,
):
    """What the fake knows about one file; `None` fields are left out of the reply."""
    return {
        "width": width,
        "height": height,
        "mime": mime,
        "licence": licence,
        "licence_name": licence_name,
        "licence_url": licence_url,
        "artist": artist,
        "marks": tuple(marks),
        "thumb": thumb,
        "sha1": sha1,
    }


def _meta(value):
    return {"value": value, "source": "commons-desc-page"}


def _page(title, info):
    name = title.removeprefix("File:").replace(" ", "_")
    extmetadata = {}
    if info["artist"] is not None:
        extmetadata["Artist"] = _meta(info["artist"])
    if info["licence"] is not None:
        extmetadata["License"] = _meta(info["licence"])
    if info["licence_name"] is not None:
        extmetadata["LicenseShortName"] = _meta(info["licence_name"])
    if info["licence_url"] is not None:
        extmetadata["LicenseUrl"] = _meta(info["licence_url"])
    imageinfo = {
        "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{name}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}",
        "width": info["width"],
        "height": info["height"],
        "mime": info["mime"],
        "sha1": info["sha1"],
        "extmetadata": extmetadata,
    }
    if info["thumb"]:
        imageinfo["thumburl"] = (
            f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{name}/1280px-{name}"
        )
    page = {"ns": 6, "title": title, "imageinfo": [imageinfo]}
    if info["marks"]:
        page["categories"] = [{"ns": 14, "title": f"Category:{m}"} for m in info["marks"]]
    return page


def fake_commons(files=None, categories=None, *, seen=None, search_seen=None, total=None):
    """Answer imageinfo+categories batches from a title -> commons_file() table (titles keep their
    `File:` prefix) and category searches from a category -> [file titles] table.

    Redirects are not simulated; unknown titles come back `missing`. `total` overrides the
    search's `totalhits` (to show a capped category).
    """
    files = files or {}
    categories = categories or {}

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("list") == "search":
            query = params["srsearch"]
            if search_seen is not None:
                search_seen.append(query)
            name = query.split('deepcat:"', 1)[1].split('"', 1)[0]
            titles = categories.get(name, [])
            hits = [{"ns": 6, "title": t} for t in titles][::-1]
            return httpx.Response(
                200,
                json={
                    "batchcomplete": True,
                    "query": {
                        "searchinfo": {"totalhits": total if total is not None else len(titles)},
                        "search": hits,
                    },
                },
            )
        titles = params["titles"].split("|")
        if seen is not None:
            seen.append(titles)
        pages = []
        for title in titles:
            if title in files:
                pages.append(_page(title, files[title]))
            else:
                pages.append({"ns": 6, "title": title, "missing": True})
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": pages[::-1]}})

    return answer
