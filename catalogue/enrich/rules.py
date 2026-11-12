"""The B4 rules file, `config/enrich_rules.toml`. Every value needs a written reason."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from catalogue.errors import PipelineError

ENRICH_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "enrich_rules.toml"


@dataclass(frozen=True, slots=True)
class EnrichRules:
    version: int
    disagreement_km: float
    max_precision_degrees: float
    reasons: dict[str, str] = field(default_factory=dict)


def load_enrich_rules(path: Path = ENRICH_RULES_PATH) -> EnrichRules:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PipelineError(f"{path.name} needs an integer rules_version")
    coordinates = document.get("coordinates")
    rule = coordinates.get("disagreement") if isinstance(coordinates, dict) else None
    km = rule.get("km") if isinstance(rule, dict) else None
    reason = rule.get("reason") if isinstance(rule, dict) else None
    if isinstance(km, bool) or not isinstance(km, int | float) or km <= 0:
        raise PipelineError(f"{path.name}: coordinates.disagreement needs a positive km")
    if not isinstance(reason, str) or not reason.strip():
        raise PipelineError(f"{path.name}: coordinates.disagreement needs a written reason")
    precision = coordinates.get("max_precision") if isinstance(coordinates, dict) else None
    degrees = precision.get("degrees") if isinstance(precision, dict) else None
    why = precision.get("reason") if isinstance(precision, dict) else None
    if isinstance(degrees, bool) or not isinstance(degrees, int | float) or not 0 < degrees < 1:
        raise PipelineError(f"{path.name}: coordinates.max_precision needs degrees between 0 and 1")
    if not isinstance(why, str) or not why.strip():
        raise PipelineError(f"{path.name}: coordinates.max_precision needs a written reason")
    return EnrichRules(
        version,
        float(km),
        float(degrees),
        {"disagreement_km": reason.strip(), "max_precision_degrees": why.strip()},
    )
