"""The rules file for `photo-candidates` (`config/photo_rules.toml`).

Rules only decide what is gathered and what is flagged. Every value that matters carries a written
reason in the file, and the file has a `rules_version` that goes into each run's manifest.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError

PHOTO_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "photo_rules.toml"


@dataclass(frozen=True, slots=True)
class PhotoRules:
    version: int
    licence_patterns: tuple[re.Pattern[str], ...]
    licence_names: frozenset[str]
    mime_types: frozenset[str]
    thumb_width: int
    quality_categories: tuple[str, ...]
    category_cap: int
    reasons: dict[str, str] = field(default_factory=dict)

    def licence_allowed(self, code: str) -> bool:
        lowered = code.strip().lower()
        return any(pattern.fullmatch(lowered) for pattern in self.licence_patterns)

    def licence_name_allowed(self, name: str) -> bool:
        """For a file with no licence code: is its short name on the allowed list (exact)?"""
        return name.strip() in self.licence_names


def _table(document: dict[str, Any], name: str, source: str) -> dict[str, Any]:
    table = document.get(name)
    if not isinstance(table, dict):
        raise PipelineError(f"{source}: the [{name}] table is missing")
    return table


def _text(table: dict[str, Any], key: str, source: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{source}: {key} needs a written text")
    return value.strip()


def _strings(table: dict[str, Any], key: str, source: str) -> list[str]:
    value = table.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise PipelineError(f"{source}: {key} needs a non-empty list of strings")
    return value


def _whole(table: dict[str, Any], key: str, source: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PipelineError(f"{source}: {key} needs a positive whole number")
    return value


def load_photo_rules(path: Path = PHOTO_RULES_PATH) -> PhotoRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PipelineError(f"{source} needs an integer rules_version")
    licences = _table(document, "licences", source)
    files = _table(document, "files", source)
    commons = _table(document, "commons", source)
    try:
        patterns = tuple(re.compile(p) for p in _strings(licences, "allowed", source))
    except re.error:
        raise PipelineError(f"{source}: a licence pattern is not a valid expression") from None
    return PhotoRules(
        version=version,
        licence_patterns=patterns,
        licence_names=frozenset(_strings(licences, "allowed_names", source)),
        mime_types=frozenset(_strings(files, "mime_types", source)),
        thumb_width=_whole(files, "thumb_width", source),
        quality_categories=tuple(_strings(commons, "quality_categories", source)),
        category_cap=_whole(commons, "category_cap", source),
        reasons={
            "licences": _text(licences, "reason", source),
            "mime_types": _text(files, "mime_reason", source),
            "thumb_width": _text(files, "thumb_reason", source),
            "category_cap": _text(commons, "cap_reason", source),
        },
    )
