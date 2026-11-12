"""Read what `discover` and `fetch-listings` wrote, after checking that the two belong together.

`verify_inputs` compares SHA-256 hashes against the fetch-listings manifest, so a stale or edited
file is refused instead of producing facts that do not match their rows. Error messages name files
only: a damaged row's text is never quoted (the logs are public).
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError


@dataclass(frozen=True, slots=True)
class ArticleIn:
    page_id: int
    title: str
    type: str | None
    role: str
    wikidata_id: str | None
    tree_country_iso: str | None
    tree_country: str | None = None


@dataclass(frozen=True, slots=True)
class ListingIn:
    page_id: int
    ordinal: int
    listing_key: str
    kind: str
    name: str
    wikidata: str
    wikipedia: str
    lat: str
    long: str
    page_type: str | None
    page_lat: str
    page_long: str
    country_iso: str | None
    from_marker: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        raise PipelineError(f"{path.name} not found in {path.parent}")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise PipelineError(f"{path.name} has a damaged row") from None
            if not isinstance(row, dict):
                raise PipelineError(f"{path.name} has a damaged row")
            yield row


def verify_inputs(
    discover_folder: Path, listings_folder: Path, *, country_filter: str | None
) -> None:
    manifest_path = listings_folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(
            f"manifest.json not found in {listings_folder}; run fetch-listings first"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        articles_sha = manifest["articles_sha256"]
        outputs = manifest["outputs_sha256"]
        recorded_country = manifest["country_filter"]
    except (ValueError, KeyError, TypeError):
        raise PipelineError(
            "the fetch-listings manifest.json is damaged; run fetch-listings again"
        ) from None
    if recorded_country != country_filter:
        raise PipelineError(
            "fetch-listings was run for a different country filter; "
            "run enrich-wikidata with the same --country"
        )
    articles_path = discover_folder / "articles.jsonl"
    if not articles_path.is_file():
        raise PipelineError(f"articles.jsonl not found in {discover_folder}; run discover first")
    if _sha256(articles_path) != articles_sha:
        raise PipelineError("articles.jsonl changed since fetch-listings ran; run it again")
    for name in ("listings.jsonl", "sections.jsonl"):
        path = listings_folder / name
        if not path.is_file():
            raise PipelineError(f"{name} not found in {listings_folder}")
        if _sha256(path) != outputs.get(name):
            raise PipelineError(f"{name} does not match the fetch-listings manifest")


def read_articles(discover_folder: Path) -> list[ArticleIn]:
    articles = []
    for row in _rows(discover_folder / "articles.jsonl"):
        try:
            articles.append(
                ArticleIn(
                    page_id=row["wikivoyage_page_id"],
                    title=row["title"],
                    type=row.get("type"),
                    role=row["role"],
                    wikidata_id=row.get("wikidata_id"),
                    tree_country_iso=row.get("tree_country_iso"),
                    tree_country=row.get("tree_country"),
                )
            )
        except KeyError:
            raise PipelineError("articles.jsonl has a damaged row") from None
    return sorted(articles, key=lambda a: a.page_id)


def read_destination_states(discover_folder: Path) -> dict[int, tuple[str, str | None]]:
    """Discover's confirmation state and reason for each usable-or-better destination."""
    states = {}
    for row in _rows(discover_folder / "destinations.jsonl"):
        try:
            states[row["wikivoyage_page_id"]] = (row["confirmation"], row["confirmation_reason"])
        except KeyError:
            raise PipelineError("destinations.jsonl has a damaged row") from None
    return states


def read_page_geo(listings_folder: Path) -> dict[int, tuple[str, str]]:
    """Each page's own `{{geo}}` as the raw strings fetch-listings copied onto its section rows
    (the first row of a page decides; empty strings when the page has none)."""
    geo: dict[int, tuple[str, str]] = {}
    for row in _rows(listings_folder / "sections.jsonl"):
        try:
            page_id = row["page_id"]
        except KeyError:
            raise PipelineError("sections.jsonl has a damaged row") from None
        if page_id not in geo:
            geo[page_id] = (row.get("page_lat") or "", row.get("page_long") or "")
    return geo


def iter_listings(listings_folder: Path) -> Iterator[ListingIn]:
    for row in _rows(listings_folder / "listings.jsonl"):
        try:
            yield ListingIn(
                page_id=row["page_id"],
                ordinal=row["ordinal"],
                listing_key=row["listing_key"],
                kind=row["kind"],
                name=row["name"],
                wikidata=row["wikidata"],
                wikipedia=row["wikipedia"],
                lat=row["lat"],
                long=row["long"],
                page_type=row["page_type"],
                page_lat=row.get("page_lat") or "",
                page_long=row.get("page_long") or "",
                country_iso=row["country_iso"],
                from_marker=bool(row["from_marker"]),
            )
        except KeyError:
            raise PipelineError("listings.jsonl has a damaged row") from None


def verify_enrich_output(
    folder: Path, country_filter: str | None, names: tuple[str, ...]
) -> tuple[str, dict[str, str]]:
    """Check an `enrich-wikidata` output folder belongs to this run: its manifest must exist, name
    the same country filter and carry the SHA-256 of each file in `names`. Returns the manifest's
    own hash and the input hashes, for the next job's manifest."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"manifest.json not found in {folder}; run enrich-wikidata first")
    damaged = "the enrich-wikidata manifest.json is damaged; run enrich-wikidata again"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded_country = manifest["country_filter"]
        recorded = manifest["outputs_sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(damaged) from None
    if not isinstance(recorded, dict):
        raise PipelineError(damaged)
    if recorded_country != country_filter:
        raise PipelineError(
            "enrich-wikidata was run for a different country filter; use the same --country"
        )
    hashes: dict[str, str] = {}
    for name in names:
        path = folder / name
        if not path.is_file():
            raise PipelineError(f"{name} not found in {folder}")
        try:
            hashes[name] = _sha256(path)
        except OSError:
            raise PipelineError(f"{name} could not be read") from None
        if hashes[name] != recorded.get(name):
            raise PipelineError(f"{name} does not match the enrich-wikidata manifest")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest(), hashes
