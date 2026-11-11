"""Quality checks for listings. They flag; they never drop or change a listing's data.

- Coordinates: missing, invalid, unchecked (the page has no usable `{{geo}}`) or far from the
  page's own point. How far is "far" per page type lives in `config/listing_quality.toml`, each
  threshold with a written reason, set from the real distance percentiles in the report.
- Shape: a URL, email address, name or Wikidata ID that is malformed.
- The Wikidata gap: listings without coordinates, grouped by what could fill them in later.
- Duplicates inside one owner (a city plus its districts), and the same place under two kinds.
"""

import math
import re
import tomllib
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from catalogue.errors import PipelineError
from catalogue.listings.keys import words

EARTH_RADIUS_KM = 6371.0088
SAME_PLACE_KM = 0.15
RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "listing_quality.toml"
_WIKIDATA = re.compile(r"[Qq][1-9][0-9]*")
_EMAIL = re.compile(r"[^@\s,;:<>]+@[^@\s,;:<>]+\.[^@\s,;:<>]+")
_EMAIL_SEPARATOR = re.compile(r"\s*[,;]\s*")
_NAME_MARKUP = re.compile(r"\[\[|\]\]|\{\{|\}\}|<[^>]+>|''")


def parse_coordinate(lat: str, long: str) -> tuple[float, float] | None:
    """(latitude, longitude) when both are real numbers in range and not the 0,0 placeholder."""
    try:
        latitude, longitude = float(lat.strip()), float(long.strip())
    except ValueError:
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if (latitude, longitude) == (0.0, 0.0):
        return None
    return latitude, longitude


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometres (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


# --- the rules file -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityRules:
    version: int
    far_km: dict[str, float]  # page type -> kilometres; a type not listed is not distance-checked
    reasons: dict[str, str] = field(default_factory=dict)


def load_rules(path: Path = RULES_PATH) -> QualityRules:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PipelineError(f"{path.name} needs an integer rules_version")
    far: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for page_type, rule in document.get("coordinates", {}).get("far_km", {}).items():
        km = rule.get("km") if isinstance(rule, dict) else None
        reason = rule.get("reason") if isinstance(rule, dict) else None
        if isinstance(km, bool) or not isinstance(km, int | float) or km <= 0:
            raise PipelineError(f"{path.name}: {page_type} needs a positive km")
        if not isinstance(reason, str) or not reason.strip():
            raise PipelineError(f"{path.name}: {page_type} needs a written reason")
        far[page_type], reasons[page_type] = float(km), reason.strip()
    return QualityRules(version, far, reasons)


# --- coordinates --------------------------------------------------------------------------------


def coordinate_flags(
    lat: str, long: str, *, distance_km: float | None, page_type: str | None, rules: QualityRules
) -> list[str]:
    if not lat.strip() and not long.strip():
        return ["coord_missing"]
    if parse_coordinate(lat, long) is None:
        return ["coord_invalid"]
    if distance_km is None:
        return ["coord_unchecked"]
    limit = rules.far_km.get(page_type or "")
    return ["coord_far"] if limit is not None and distance_km > limit else []


# --- shape checks -------------------------------------------------------------------------------


def _url_ok(url: str) -> bool:
    if any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and "." in (host or "")


def shape_flags(fields: Mapping[str, str]) -> list[str]:
    found: list[str] = []
    name = fields["name"].strip()
    if not name:
        found.append("name_missing")
    elif _NAME_MARKUP.search(name):
        found.append("name_markup")
    url = fields["url"].strip()
    if url and not _url_ok(url):
        found.append("url_invalid")
    email = fields["email"].strip()
    if email and not all(_EMAIL.fullmatch(part) for part in _EMAIL_SEPARATOR.split(email)):
        found.append("email_invalid")
    wikidata = fields["wikidata"].strip()
    if wikidata and not _WIKIDATA.fullmatch(wikidata):
        found.append("wikidata_invalid")
    return found


def valid_wikidata(value: str) -> str:
    """The upper-case Wikidata ID, or an empty string when there is none or it is malformed."""
    value = value.strip()
    return value.upper() if _WIKIDATA.fullmatch(value) else ""


# --- the Wikidata gap ---------------------------------------------------------------------------


def gap_group(*, has_coordinates: bool, wikidata: str, wikipedia: str) -> str | None:
    """For a listing without usable coordinates: what could fill them in later.
    `has_wikidata` (a Wikidata ID names it), `wikipedia_only` (a Wikipedia link could be resolved
    to an ID) or `neither`. Address text is never geocoded."""
    if has_coordinates:
        return None
    if valid_wikidata(wikidata):
        return "has_wikidata"
    return "wikipedia_only" if wikipedia.strip() else "neither"


# --- duplicates ---------------------------------------------------------------------------------


class DupInput(NamedTuple):
    scope: int  # the owner's page ID
    kind: str
    name: str  # normalised, see `normalised_name`
    wikidata: str  # valid upper-case ID or ""
    point: tuple[float, float] | None
    address: str  # `address_label` of the address, or ""
    page_id: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class DupResult:
    # (page_id, ordinal) of the later listing -> ((page_id, ordinal) of the original, basis)
    duplicates: dict[tuple[int, int], tuple[tuple[int, int], str]]
    same_place: dict[tuple[int, int], tuple[int, int]]


def normalised_name(name: str) -> str:
    """Case, accents (on Latin letters only) and punctuation ignored; other scripts keep their
    vowel signs and marks. An empty result means there is no usable name."""
    kept: list[str] = []
    latin_base = False
    for char in unicodedata.normalize("NFKD", name):
        category = unicodedata.category(char)
        if category == "Mn" and latin_base:
            continue
        kept.append(char)
        if category[0] == "L":
            latin_base = unicodedata.name(char, "").startswith("LATIN")
        elif category != "Mn":
            latin_base = False
    return " ".join(words("".join(kept)))


def _basis(earlier: DupInput, later: DupInput) -> str | None:
    if earlier.wikidata and later.wikidata:
        return "wikidata" if earlier.wikidata == later.wikidata else None
    if earlier.point and later.point:
        close = distance_km(earlier.point, later.point) <= SAME_PLACE_KM
        return "name_and_coords" if close else None  # far apart: another branch of the same name
    if earlier.address and later.address:
        # No coordinates to compare: the same address is a duplicate, another address is another
        # branch (two Pizza Huts in one city).
        return "name_and_address" if earlier.address == later.address else None
    return "name_only"  # nothing to compare; low confidence


def find_duplicates(entries: Iterable[DupInput]) -> DupResult:
    """Inside one owner, a listing repeating an earlier one of the same kind and name. The result
    does not depend on the order of `entries`; "earlier" means lower (page_id, ordinal)."""
    by_name: dict[tuple[int, str, str], list[DupInput]] = defaultdict(list)
    by_wikidata: dict[tuple[int, str], list[DupInput]] = defaultdict(list)
    for entry in entries:
        if entry.name:
            by_name[(entry.scope, entry.kind, entry.name)].append(entry)
        if entry.wikidata:
            by_wikidata[(entry.scope, entry.wikidata)].append(entry)

    duplicates: dict[tuple[int, int], tuple[tuple[int, int], str]] = {}
    for members in by_name.values():
        members.sort(key=lambda m: (m.page_id, m.ordinal))
        for index, later in enumerate(members[1:], start=1):
            for earlier in members[:index]:
                basis = _basis(earlier, later)
                if basis:
                    duplicates[(later.page_id, later.ordinal)] = (
                        (earlier.page_id, earlier.ordinal),
                        basis,
                    )
                    break

    same_place: dict[tuple[int, int], tuple[int, int]] = {}
    for members in by_wikidata.values():
        members.sort(key=lambda m: (m.page_id, m.ordinal))
        for index, later in enumerate(members):
            other = next((e for e in members[:index] if e.kind != later.kind), None)
            if other:
                same_place[(later.page_id, later.ordinal)] = (other.page_id, other.ordinal)
    return DupResult(duplicates, same_place)


def rules_summary(rules: QualityRules) -> dict[str, Any]:
    return {"rules_version": rules.version, "far_km": dict(sorted(rules.far_km.items()))}
