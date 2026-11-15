"""Size and file-count limits for the static site (config/publish_limits.toml)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from catalogue.errors import PipelineError

LIMITS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "publish_limits.toml"


@dataclass(frozen=True, slots=True)
class PublishLimits:
    max_files: int
    max_file_bytes: int
    max_index_bytes: int = 2 * 1024 * 1024
    attribution_part_bytes: int = 4 * 1024 * 1024


def load_limits(path: Path = LIMITS_PATH) -> PublishLimits:
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))["limits"]
        values = {
            k: table[k]
            for k in ("max_files", "max_file_bytes", "max_index_bytes", "attribution_part_bytes")
        }
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise PipelineError(f"{path.name} is missing or damaged") from None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values.values()):
        raise PipelineError(f"{path.name} limits must be positive whole numbers")
    return PublishLimits(**values)
