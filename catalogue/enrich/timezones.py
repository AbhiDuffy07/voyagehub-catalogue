"""`derive-timezone` (B4c): the IANA time zone of each destination, from its coordinates.

`destinations.timezone` cannot be empty, but after `enrich-wikidata` about 59% of destination rows
had no zone: countries with several zones, and single-zone countries such as Germany whose Wikidata
zone items carry no IANA name. This step reads the hash-checked `enrich-wikidata` output and, only
where Wikidata gave no single zone, looks the zone up from the destination's coordinates in
time-zone boundary data (the `tzfpy` package, data from timezone-boundary-builder).

Rules, none of them a guess:
- The place's own Wikidata zone is kept and never replaced; where the coordinates give a zone
  with a different offset the row says `timezone_disagrees` and both are shown.
- The country's single zone (a labelled B4a fallback, wrong for Canada, Spain, ...) gives way to
  the coordinates when they give one clear zone: `timezone_replaced_country_zone`.
- Zones are labelled `timezone_basis: coordinates`, with the boundary data version and the credit.
- A point at sea (`Etc/GMT...`), outside every boundary, in two overlapping zones (a disputed
  border) or answered with a name the time-zone database does not know gets no zone and a flag.
- A destination with no coordinate and no Wikidata zone stays empty (`timezone_unknown`).

Offline and fast: no network, no settings. Local files only, in `out/timezones/<date>[-CC]/`.
Only zone names are stored, with this credit wherever they are published:
"Time zones: timezone-boundary-builder, © OpenStreetMap contributors, ODbL" (ODbL 1.0).
"""

import hashlib
import importlib.metadata
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

import tzfpy

from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.enrich.inputs import _rows, verify_enrich_output
from catalogue.errors import PipelineError
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import _safe_cell, parse_dump_date
from catalogue.progress import Progress

SCHEMA_VERSION = 1
ATTRIBUTION = "Time zones: timezone-boundary-builder, © OpenStreetMap contributors, ODbL"
LICENCE = "ODbL 1.0"
DESTINATION_ROLES = frozenset({"destination", "destination_outline"})
NO_COUNTRY = "(none)"
# Two zone names with the same UTC offset at all of these instants (both sides of daylight saving,
# two years) are the same zone for our purposes: US/Eastern and America/Toronto, Asia/Calcutta and
# Asia/Kolkata.
_SAMPLE_INSTANTS = tuple(
    datetime(year, month, 15, 12, tzinfo=UTC) for year in (2026, 2027) for month in (1, 7)
)
OUTPUT_FILES = (
    "destination_timezones.jsonl",
    "report.md",
    "timezone_disagreements.csv",
    "timezones_by_country.csv",
)


def lookup_zones(lat: float, lon: float) -> tuple[str, ...]:
    """Every zone whose boundary holds the point. tzfpy takes (longitude, latitude); passing them
    the other way round silently gives a wrong zone, so this wrapper takes latitude first."""
    return tuple(tzfpy.get_tzs(lon, lat))


def same_offsets(first: str, second: str) -> bool:
    try:
        a, b = ZoneInfo(first), ZoneInfo(second)
    except (KeyError, ValueError, OSError, TypeError):  # ZoneInfoNotFoundError is a KeyError
        return False
    return all(i.astimezone(a).utcoffset() == i.astimezone(b).utcoffset() for i in _SAMPLE_INSTANTS)


def _coordinate_zone(
    lat: float, lon: float, locate: Callable[[float, float], tuple[str, ...]], known: frozenset[str]
) -> tuple[str | None, str | None]:
    """(zone, None) for exactly one known land zone, else (None, the reason as a flag)."""
    land = tuple(sorted({z for z in locate(lat, lon) if not z.startswith("Etc/")}))
    if not land:
        return None, "timezone_no_land_zone"  # sea (Etc/GMT...) or outside every boundary
    if len(land) > 1:
        return None, "timezone_ambiguous"
    if land[0] not in known:
        return None, "timezone_invalid_name"
    return land[0], None


@dataclass(frozen=True, slots=True)
class TimezoneResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        lines = [
            f"destinations: {t['destinations']}; zone from Wikidata: {t['from_wikidata_item']} "
            f"(own) + {t['from_country_single_zone']} (country); from coordinates: "
            f"{t['from_coordinates']}; still without a zone: {t['still_none']}",
            f"country zone corrected by the coordinates: {t['replaced_country_zone']} "
            f"(+ {t['alias_normalised']} renamed to the exact name); "
            f"own Wikidata zone contradicted: {t['disagreements']}",
        ]
        if t["flags"]:
            lines.append(", ".join(f"{k} {v}" for k, v in t["flags"].items()))
        return "\n".join(lines)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _point(row: dict[str, Any]) -> tuple[float | None, float | None, bool]:
    """(latitude, longitude, bad). One coordinate alone, or none, is simply no point. A value that
    is not a finite number or lies outside the globe is bad data: no lookup, and never echoed."""
    raw_lat, raw_lon = row.get("coord_lat"), row.get("coord_long")
    if raw_lat is None or raw_lon is None:
        return None, None, False
    lat, lon = _number(raw_lat), _number(raw_lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None, True
    return lat, lon, False


def _destination_rows(folder: Path) -> list[dict[str, Any]]:
    rows = []
    for row in _rows(folder / "destination_facts.jsonl"):
        try:
            page_id, role = row["wikivoyage_page_id"], row["role"]
        except KeyError:
            raise PipelineError("destination_facts.jsonl has a damaged row") from None
        if not isinstance(page_id, int):
            raise PipelineError("destination_facts.jsonl has a damaged row")
        if role in DESTINATION_ROLES:
            rows.append(row)
    return sorted(rows, key=lambda r: r["wikivoyage_page_id"])


def _decide(
    row: dict[str, Any],
    locate: Callable[[float, float], tuple[str, ...]],
    known: frozenset[str],
    data_version: str,
) -> dict[str, Any]:
    wikidata_name = row.get("timezone") if isinstance(row.get("timezone"), str) else None
    existing, existing_basis = wikidata_name or None, row.get("timezone_basis")
    lat, lon, bad = _point(row)
    has_point = lat is not None and lon is not None
    candidate, problem = (None, None)
    if has_point:
        candidate, problem = _coordinate_zone(lat, lon, locate, known)  # type: ignore[arg-type]

    flags: list[str] = []
    if existing and existing not in known:
        # Wikidata's P6687 is a free string; a name the time-zone database does not know is not
        # trusted, whatever its basis, and the coordinates get their chance.
        flags.append("timezone_invalid_wikidata_name")
        existing = None
    if existing and existing_basis == "country_single_zone":
        # The country's only IANA zone is a guess for a place inside it (Wikidata names `US/Eastern`
        # as Canada's one zone, which would put Vancouver on Eastern time). A single clear zone from
        # the destination's own coordinates is better evidence, so it replaces the guess; the row
        # keeps the Wikidata value and says so.
        if candidate and candidate != existing:
            zone, basis = candidate, "coordinates"
            # A different name for the same offsets is only a rename; a different offset is a
            # real correction. They are counted apart so the correction count means something.
            flags.append(
                "timezone_alias_normalised"
                if same_offsets(candidate, existing)
                else "timezone_replaced_country_zone"
            )
        else:
            zone, basis = existing, existing_basis
    elif existing:
        # The place's own Wikidata zone is never replaced; a different offset is only flagged.
        zone, basis = existing, existing_basis
        if candidate and not same_offsets(candidate, existing):
            flags.append("timezone_disagrees")
    elif candidate:
        zone, basis = candidate, "coordinates"
    else:
        zone, basis = None, None
        flags.append(
            problem
            if has_point and problem
            else "timezone_bad_coordinates"
            if bad
            else "timezone_unknown"
        )

    derived = basis == "coordinates"
    return {
        "schema_version": SCHEMA_VERSION,
        "wikivoyage_page_id": row["wikivoyage_page_id"],
        "title": row.get("title"),
        "tree_country_iso": row.get("tree_country_iso"),
        "timezone": zone,
        "timezone_basis": basis,
        "wikidata_timezone": wikidata_name or None,
        "coordinate_timezone": candidate,
        "coord_lat": lat,
        "coord_long": lon,
        "coord_source": row.get("coord_source"),
        "timezone_boundary_data_version": data_version if derived else None,
        "timezone_licence": LICENCE if derived else None,
        "timezone_attribution": ATTRIBUTION if derived else None,
        "flags": flags,
    }


def run_derive_timezone(
    *,
    enrich_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    locate: Callable[[float, float], tuple[str, ...]] | None = None,
    data_version: str | None = None,
) -> TimezoneResult:
    parse_dump_date(dump_date)
    country_filter = country_iso.upper() if country_iso else None
    manifest_sha, input_hashes = verify_enrich_output(
        enrich_folder, country_filter, ("destination_facts.jsonl",)
    )
    locate = locate if locate is not None else lookup_zones
    data_version = data_version if data_version is not None else tzfpy.data_version()
    known = frozenset(available_timezones())
    destinations = _destination_rows(enrich_folder)

    decided: list[dict[str, Any]] = []
    progress = Progress("destinations", len(destinations))
    for row in destinations:
        decided.append(_decide(row, locate, known, data_version))
        progress.advance()
    progress.done()

    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    with JsonlWriter(target / "destination_timezones.jsonl") as out:
        for result_row in decided:
            out.write(result_row)

    counts: dict[str, Counter] = {}
    flag_counts: Counter = Counter()
    disagreements: list[list[Any]] = []
    for r in decided:
        country = r["tree_country_iso"] or NO_COUNTRY
        c = counts.setdefault(country, Counter())
        c["destinations"] += 1
        c[r["timezone_basis"] or "none"] += 1
        if "timezone_replaced_country_zone" in r["flags"]:
            c["replaced_country_zone"] += 1
        if "timezone_alias_normalised" in r["flags"]:
            c["alias_normalised"] += 1
        if "timezone_disagrees" in r["flags"]:
            c["disagreements"] += 1
            disagreements.append(
                [
                    r["wikivoyage_page_id"],
                    _safe_cell(r["title"] or ""),
                    _safe_cell(country),
                    _safe_cell(r["wikidata_timezone"] or ""),
                    _safe_cell(r["coordinate_timezone"] or ""),
                ]
            )
        flag_counts.update(r["flags"])

    def total(key: str) -> int:
        return sum(c[key] for c in counts.values())

    totals = {
        "destinations": len(decided),
        "from_wikidata_item": total("wikidata_item"),
        "from_country_single_zone": total("country_single_zone"),
        "from_coordinates": total("coordinates"),
        "still_none": total("none"),
        "replaced_country_zone": total("replaced_country_zone"),
        "alias_normalised": total("alias_normalised"),
        "disagreements": total("disagreements"),
        "flags": dict(sorted(flag_counts.items())),
    }
    write_text(
        target / "timezones_by_country.csv",
        _csv_text(
            [
                "country_iso",
                "destinations",
                "from_wikidata_item",
                "from_country_single_zone",
                "from_coordinates",
                "still_none",
                "replaced_country_zone",
                "alias_normalised",
                "disagreements",
            ],
            [
                [
                    _safe_cell(country),
                    c["destinations"],
                    c["wikidata_item"],
                    c["country_single_zone"],
                    c["coordinates"],
                    c["none"],
                    c["replaced_country_zone"],
                    c["alias_normalised"],
                    c["disagreements"],
                ]
                for country, c in sorted(counts.items())
            ],
        ),
    )
    write_text(
        target / "timezone_disagreements.csv",
        _csv_text(
            ["page_id", "title", "country_iso", "wikidata_timezone", "coordinate_timezone"],
            disagreements,
        ),
    )
    write_text(target / "report.md", _report(totals, data_version, dump_date))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "boundary_data_version": data_version,
            "tzfpy_version": importlib.metadata.version("tzfpy"),
            "attribution": ATTRIBUTION,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": today.isoformat(),
            "enrich_manifest_sha256": manifest_sha,
            "inputs_sha256": input_hashes,
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return TimezoneResult(target, totals)


def _report(totals: dict[str, Any], data_version: str, dump_date: str) -> str:
    lines = [
        f"# derive-timezone, dump {dump_date}",
        "",
        f"Boundary data version: {data_version}. Credit: {ATTRIBUTION} (ODbL 1.0).",
        "",
        f"- destinations: {totals['destinations']}",
        f"- zone from Wikidata (own): {totals['from_wikidata_item']}",
        f"- zone from Wikidata (country's single zone): {totals['from_country_single_zone']}",
        f"- zone from coordinates: {totals['from_coordinates']}",
        f"- still without a zone: {totals['still_none']}",
        f"- country's single zone corrected by the coordinates: {totals['replaced_country_zone']}",
        f"- country's single zone renamed (same offsets): {totals['alias_normalised']}",
        f"- the place's own Wikidata zone contradicted: {totals['disagreements']}",
        "",
        "## Flags",
        "",
    ]
    lines += [f"- `{flag}`: {count}" for flag, count in totals["flags"].items()] or ["- none"]
    lines += [
        "",
        "A Wikidata zone is never replaced; `timezone_disagrees` rows are listed in "
        "`timezone_disagreements.csv` for review.",
        "",
    ]
    return "\n".join(lines)
