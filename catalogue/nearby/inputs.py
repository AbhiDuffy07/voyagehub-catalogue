"""Reading a `nearby` (B18) output folder in the jobs after it (photo-select, readiness,
publish-static): the manifest must name the same country filter and the files must match their
hashes, so every job sees exactly the same extra places and candidates."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.enrich.inputs import _rows, _sha256
from catalogue.errors import PipelineError

PLACES_FILE = "nearby_places.jsonl"
CANDIDATES_FILE = "nearby_candidates.jsonl"


@dataclass(frozen=True, slots=True)
class NearbyFolder:
    folder: Path
    manifest_sha256: str
    places_sha256: str
    candidates_sha256: str
    photo_candidates_manifest_sha256: str


def verify_nearby(folder: Path, country_filter: str | None) -> NearbyFolder:
    path = folder / "manifest.json"
    if not path.is_file():
        raise PipelineError(f"manifest.json not found in {folder}; run nearby first")
    damaged = "the nearby manifest.json is damaged; run nearby again"
    try:
        data = path.read_bytes()
        manifest = json.loads(data)
        recorded_country = manifest["country_filter"]
        outputs = manifest["outputs_sha256"]
        upstream = manifest["photo_candidates_manifest_sha256"]
        places_recorded = outputs[PLACES_FILE]
        candidates_recorded = outputs[CANDIDATES_FILE]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(damaged) from None
    if recorded_country != country_filter:
        raise PipelineError("nearby was run for a different country filter; use the same --country")
    hashes: dict[str, str] = {}
    for name, recorded in ((PLACES_FILE, places_recorded), (CANDIDATES_FILE, candidates_recorded)):
        file = folder / name
        if not file.is_file():
            raise PipelineError(f"{name} not found in {folder}")
        hashes[name] = _sha256(file)
        if hashes[name] != recorded:
            raise PipelineError(f"{name} does not match the nearby manifest")
    return NearbyFolder(
        folder,
        hashlib.sha256(data).hexdigest(),
        hashes[PLACES_FILE],
        hashes[CANDIDATES_FILE],
        str(upstream),
    )


def extra_places(nearby: NearbyFolder) -> list[dict[str, Any]]:
    return list(_rows(nearby.folder / PLACES_FILE))


def nearby_candidates(nearby: NearbyFolder) -> list[dict[str, Any]]:
    return list(_rows(nearby.folder / CANDIDATES_FILE))


def nearby_for(
    photos_manifest: dict[str, Any], nearby_folder: Path | None, country_filter: str | None
) -> NearbyFolder | None:
    """The nearby folder a job after photo-select must read: exactly the one photo-select read
    (its manifest records the places hash), or none when photo-select read none."""
    recorded = photos_manifest.get("nearby_places_sha256")
    if nearby_folder is None:
        if recorded:
            raise PipelineError(
                "photo-select used nearby places; pass the same nearby folder (--nearby-dir)"
            )
        return None
    nearby = verify_nearby(nearby_folder, country_filter)
    if recorded is None:
        raise PipelineError(
            "photo-select was run without this nearby folder; run photo-select with --nearby-dir"
        )
    if recorded != nearby.places_sha256:
        raise PipelineError("photo-select was run with other nearby places; run photo-select again")
    return nearby
