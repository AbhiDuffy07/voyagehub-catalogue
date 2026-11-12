"""`config/kind_map.toml`: which Wikidata classes (P31 values) mean which place kind.

The file is global (no country in it) and written by hand from the real classes the India run
found most often, each class label checked against live Wikidata. A class not listed gives no
kind: the row keeps its raw P31 values, and the most common unmapped classes are reported.

    [kinds]
    museum = ["Q33506"]
"""

import re
import tomllib
from pathlib import Path

from catalogue.errors import PipelineError

KIND_MAP_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "kind_map.toml"

# The `place_kind` values the database accepts, minus `other`, which is what an unmapped place
# gets by default and so is never mapped on purpose.
MAPPABLE_KINDS = frozenset(
    {
        "sight",
        "museum",
        "religious",
        "fort_palace",
        "park_nature",
        "market",
        "viewpoint",
        "neighbourhood",
        "activity",
    }
)
_QID = re.compile(r"Q[1-9][0-9]*")


def load_kind_map(path: Path = KIND_MAP_PATH) -> dict[str, str]:
    """Class Q-ID -> kind. A class under two kinds, an unknown kind or a malformed ID is refused."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{path.name} could not be read as a TOML file") from None
    table = document.get("kinds", {})
    if not isinstance(table, dict):
        raise PipelineError(f"{path.name}: [kinds] must be a table")
    mapping: dict[str, str] = {}
    for kind, classes in table.items():
        if kind not in MAPPABLE_KINDS:
            raise PipelineError(f"{path.name}: {kind!r} is not a mappable place kind")
        if not isinstance(classes, list):
            raise PipelineError(f"{path.name}: {kind} must be a list of Q-IDs")
        for qid in classes:
            if not isinstance(qid, str) or not _QID.fullmatch(qid):
                raise PipelineError(f"{path.name}: {kind} has a malformed Q-ID")
            if qid in mapping:
                raise PipelineError(f"{path.name}: {qid} is listed under more than one kind")
            mapping[qid] = kind
    return mapping
