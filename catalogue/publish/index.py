"""`index.json` for the app's Home and Explore screens.

While the whole index fits the size limit it is one flat file `{"cities": [...]}`. Above the limit
(a world run) it becomes `{"shards": [{"country_iso", "file", "cities"}]}` and each country's
cities move to `index/<cc>.json` (`index/unknown.json` for cities with no country), each a flat
index of its own. A shard that is still over the limit stops the build: it never publishes a file
Cloudflare Pages could refuse.
"""

from collections import defaultdict
from typing import Any

from catalogue.errors import PipelineError
from catalogue.publish.dump import dump_document
from catalogue.publish.models import SCHEMA_VERSION, IndexFile

API = "/v1"


def _shard_name(country_iso: str | None) -> str:
    return f"index/{country_iso.lower() if country_iso else 'unknown'}.json"


def index_files(cities: list[dict[str, Any]], max_bytes: int) -> dict[str, bytes]:
    """The index files by name relative to `v1/`, sorted so the output never depends on order."""
    ordered = sorted(cities, key=lambda c: c["slug"])
    flat = dump_document(
        IndexFile, {"schema_version": SCHEMA_VERSION, "cities": ordered}, "index.json"
    )
    if len(flat) <= max_bytes:
        return {"index.json": flat}
    by_country: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for city in ordered:
        by_country[city["country_iso"]].append(city)
    files: dict[str, bytes] = {}
    shards = []
    for iso in sorted(by_country, key=lambda c: (c is None, c or "")):
        name = _shard_name(iso)
        data = dump_document(
            IndexFile, {"schema_version": SCHEMA_VERSION, "cities": by_country[iso]}, name
        )
        if len(data) > max_bytes:
            raise PipelineError(
                f"{name} is {len(data)} bytes, over the {max_bytes} bytes limit; "
                "a country is too large for one index file"
            )
        files[name] = data
        shards.append(
            {
                "country_iso": iso,
                "country_name": by_country[iso][0].get("country_name"),
                "file": f"{API}/{name}",
                "cities": len(by_country[iso]),
            }
        )
    top = dump_document(
        IndexFile, {"schema_version": SCHEMA_VERSION, "shards": shards}, "index.json"
    )
    if len(top) > max_bytes:
        raise PipelineError(f"index.json is {len(top)} bytes, over the {max_bytes} bytes limit")
    return {"index.json": top, **files}
