"""`config/block_rules.toml` and `config/durations.toml`: the numbers B8 uses, with reasons."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError
from catalogue.photos.select import _number, _reason, _strings, _table

_CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
BLOCK_RULES_PATH = _CONFIG / "block_rules.toml"
DURATIONS_PATH = _CONFIG / "durations.toml"
SLOT_NAMES = ("morning", "afternoon", "full_day")


@dataclass(frozen=True, slots=True)
class TravelRules:
    detour_factor: float
    walk_kmh: float
    road_kmh: float
    max_walk_hop_min: int


@dataclass(frozen=True, slots=True)
class AreaRules:
    walk_radius_km: float


@dataclass(frozen=True, slots=True)
class BlockLimits:
    min_stops: int
    max_stops: int
    half_day_minutes: int
    full_day_minutes: int
    max_blocks_per_area: int
    day_trip_km: float
    pair_radius_km: float


@dataclass(frozen=True, slots=True)
class BlockRules:
    version: int
    owner_types: tuple[str, ...]
    sections: tuple[str, ...]
    blocked_flags: frozenset[str]
    shared_coordinate_names: int
    travel: TravelRules
    areas: AreaRules
    blocks: BlockLimits
    windows: dict[str, tuple[int, int]]
    morning_kinds: tuple[str, ...]
    afternoon_kinds: tuple[str, ...]
    reasons: dict[str, str]
    kind_durations: dict[str, tuple[int, int]]
    default_durations: dict[str, tuple[int, int]]
    duration_reasons: dict[str, str] = field(default_factory=dict)

    def duration(self, kind: str | None, section: str) -> tuple[int, int, str]:
        """(min minutes, max minutes, basis): `kind` when the table has the kind, else the
        section's default (`default`)."""
        if kind in self.kind_durations:
            low, high = self.kind_durations[kind]
            return low, high, "kind"
        low, high = self.default_durations[section if section in self.default_durations else "see"]
        return low, high, "default"


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} could not be read as a TOML file") from None


def _window(table: dict[str, Any], key: str, source: str) -> tuple[int, int]:
    value = table.get(key)
    ok = (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and 0 <= value[0] < value[1] <= 1440
    )
    if not ok:
        raise PipelineError(f"{source}: {key} needs [start, end] in minutes from midnight")
    return value[0], value[1]


def _load_durations(path: Path) -> tuple[dict, dict, dict]:
    source = path.name
    document = _read(path)
    reasons: dict[str, str] = {}
    groups: dict[str, dict[str, tuple[int, int]]] = {}
    for group in ("kinds", "defaults"):
        table = _table(document, group, source)
        groups[group] = {}
        for name, entry in table.items():
            if not isinstance(entry, dict):
                raise PipelineError(f"{source}: [{group}.{name}] must be a table")
            low = _number(entry, "min", f"{source} [{group}.{name}]", whole=True)
            high = _number(entry, "max", f"{source} [{group}.{name}]", whole=True)
            if low > high:
                raise PipelineError(f"{source}: [{group}.{name}] has min above max")
            reasons[f"{group}.{name}"] = _reason(entry, source, f"{group}.{name}")
            groups[group][name] = (low, high)
    for section in ("see", "do"):
        if section not in groups["defaults"]:
            raise PipelineError(f"{source}: [defaults.{section}] is missing")
    return groups["kinds"], groups["defaults"], reasons


def load_block_rules(
    path: Path = BLOCK_RULES_PATH, durations_path: Path = DURATIONS_PATH
) -> BlockRules:
    source = path.name
    document = _read(path)
    version = document.get("rules_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PipelineError(f"{source}: rules_version needs a whole number of at least 1")
    eligible = _table(document, "eligible", source)
    travel = _table(document, "travel", source)
    areas = _table(document, "areas", source)
    blocks = _table(document, "blocks", source)
    hours = _table(document, "hours", source)
    slots = _table(document, "slots", source)
    kinds, defaults, duration_reasons = _load_durations(durations_path)
    rules = BlockRules(
        version=version,
        owner_types=tuple(_strings(eligible, "owner_types", source)),
        sections=tuple(_strings(eligible, "sections", source)),
        blocked_flags=frozenset(_strings(eligible, "blocked_flags", source)),
        shared_coordinate_names=_number(eligible, "shared_coordinate_names", source, whole=True),
        travel=TravelRules(
            detour_factor=_number(travel, "detour_factor", source),
            walk_kmh=_number(travel, "walk_kmh", source),
            road_kmh=_number(travel, "road_kmh", source),
            max_walk_hop_min=_number(travel, "max_walk_hop_min", source, whole=True),
        ),
        areas=AreaRules(walk_radius_km=_number(areas, "walk_radius_km", source)),
        blocks=BlockLimits(
            min_stops=_number(blocks, "min_stops", source, whole=True),
            max_stops=_number(blocks, "max_stops", source, whole=True),
            half_day_minutes=_number(blocks, "half_day_minutes", source, whole=True),
            full_day_minutes=_number(blocks, "full_day_minutes", source, whole=True),
            max_blocks_per_area=_number(blocks, "max_blocks_per_area", source, whole=True),
            day_trip_km=_number(blocks, "day_trip_km", source),
            pair_radius_km=_number(blocks, "pair_radius_km", source),
        ),
        windows={name: _window(hours, name, source) for name in SLOT_NAMES},
        morning_kinds=tuple(_strings(slots, "morning", source)),
        afternoon_kinds=tuple(_strings(slots, "afternoon", source)),
        reasons={
            name: _reason(table, source, name)
            for name, table in (
                ("eligible", eligible),
                ("travel", travel),
                ("areas", areas),
                ("blocks", blocks),
                ("hours", hours),
                ("slots", slots),
            )
        },
        kind_durations=kinds,
        default_durations=defaults,
        duration_reasons=duration_reasons,
    )
    if rules.blocks.min_stops < 2 or rules.blocks.min_stops > rules.blocks.max_stops:
        raise PipelineError(f"{source}: stops per block need 2 <= min_stops <= max_stops")
    if set(rules.morning_kinds) & set(rules.afternoon_kinds):
        raise PipelineError(f"{source}: a kind is listed under both morning and afternoon")
    return rules
