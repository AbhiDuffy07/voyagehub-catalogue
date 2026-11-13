"""Choosing one main photo from a subject's B5 candidates (B6). Pure functions, no I/O but the two
small files they load: `config/photo_select.toml` and `overrides/rejected_photos.csv`.

Order of choice (owner, 2026-11-12/13): the Wikidata P18 image (preferred rank first), then
Featured, Quality and Valued files, then everything else. Candidates are first dropped (rejected by
override, not a photo type, a map/flag/logo by file name, a panorama) and then gated (long side at
least 1,024 px; portrait allowed). Inside a tier the score ranks by size and shape, with a stable
tie-break on the file name. A subject with no choosable candidate gets a flag and no photo; nothing
is guessed.
"""

import csv
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError

SELECT_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "photo_select.toml"
REJECTED_PATH = Path(__file__).resolve().parent.parent.parent / "overrides" / "rejected_photos.csv"
TIER_NAMES = ("p18", "featured", "quality", "valued", "other")
ALTERNATIVES = 2
_REJECTED_COLUMNS = {"commons_file", "reason"}


@dataclass(frozen=True, slots=True)
class SelectionRules:
    version: int
    name_pattern: re.Pattern[str]
    mime_types: frozenset[str]
    max_aspect: float
    min_long_side: int
    hero_long_side: int
    tier_marks: dict[str, frozenset[str]]
    category_min_word: int
    size_cap: int
    size_weight: float
    target_ratio: float
    shape_weight: float
    landscape_bonus: float
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Choice:
    chosen: dict[str, Any] | None
    flags: set[str]
    candidates: int  # rows B5 gave for the subject
    covered: bool  # a candidate survived the drops (before the size gate)
    tier: str | None = None
    score: float | None = None
    alternatives: list[str] = field(default_factory=list)
    long_side: int | None = None
    portrait: bool = False
    hero_quality: bool = False
    best_long_side: int | None = None  # of the survivors, when the gate removed them all


# --- the rules and override files -----------------------------------------


def _number(table: dict[str, Any], key: str, source: str, *, whole: bool = False) -> Any:
    value = table.get(key)
    ok = isinstance(value, int) if whole else isinstance(value, int | float)
    if isinstance(value, bool) or not ok or value <= 0:
        raise PipelineError(f"{source}: {key} needs a positive {'whole ' if whole else ''}number")
    return value


def _strings(table: dict[str, Any], key: str, source: str) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise PipelineError(f"{source}: {key} needs a non-empty list of strings")
    return value


def _table(document: dict[str, Any], name: str, source: str) -> dict[str, Any]:
    table = document.get(name)
    if not isinstance(table, dict):
        raise PipelineError(f"{source}: the [{name}] table is missing")
    return table


def _reason(table: dict[str, Any], source: str, name: str) -> str:
    text = table.get("reason")
    if not isinstance(text, str) or not text.strip():
        raise PipelineError(f"{source}: [{name}] needs a written reason")
    return text.strip()


def load_selection_rules(path: Path = SELECT_RULES_PATH) -> SelectionRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("rules_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PipelineError(f"{source} needs an integer rules_version")
    drop, gate = _table(document, "drop", source), _table(document, "gate", source)
    category = _table(document, "category", source)
    tiers, score = _table(document, "tiers", source), _table(document, "score", source)
    words = _strings(drop, "name_words", source)
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    return SelectionRules(
        version=version,
        name_pattern=pattern,
        mime_types=frozenset(_strings(drop, "mime_types", source)),
        max_aspect=float(_number(drop, "max_aspect", source)),
        min_long_side=_number(gate, "min_long_side", source, whole=True),
        hero_long_side=_number(gate, "hero_long_side", source, whole=True),
        category_min_word=_number(category, "min_word_length", source, whole=True),
        tier_marks={
            name: frozenset(_strings(tiers, name, source))
            for name in ("featured", "quality", "valued")
        },
        size_cap=_number(score, "size_cap", source, whole=True),
        size_weight=float(_number(score, "size_weight", source)),
        target_ratio=float(_number(score, "target_ratio", source)),
        shape_weight=float(_number(score, "shape_weight", source)),
        landscape_bonus=float(_number(score, "landscape_bonus", source)),
        reasons={"drop": _reason(drop, source, "drop"), "gate": _reason(gate, source, "gate")},
    )


def normalise_file_name(name: str) -> str:
    stripped = name.strip().replace("_", " ")
    return stripped.removeprefix("File:").strip()


def load_rejected_files(path: Path = REJECTED_PATH) -> frozenset[str]:
    """`overrides/rejected_photos.csv`: files that must never be chosen, each with a reason."""
    if not path.is_file():
        return frozenset()
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(lines)
    if set(reader.fieldnames or []) != _REJECTED_COLUMNS:
        raise PipelineError("rejected_photos.csv needs the columns commons_file, reason")
    found: set[str] = set()
    for row in reader:
        name = normalise_file_name(row["commons_file"] or "")
        if not name:
            raise PipelineError("rejected_photos.csv has a line with no file name")
        if not (row["reason"] or "").strip():
            raise PipelineError("rejected_photos.csv: every file needs a written reason")
        if name in found:
            raise PipelineError("rejected_photos.csv lists a file twice")
        found.add(name)
    return frozenset(found)


def name_words(text: str, min_length: int = 4) -> set[str]:
    """Lower-case words of at least `min_length` letters, accents folded (`Bhāratpur` is
    `bharatpur`). Digits and punctuation separate words."""
    folded = unicodedata.normalize("NFKD", text).casefold()
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return {w for w in re.findall(r"[^\W\d_]+", plain) if len(w) >= min_length}


# --- choosing -----------------------------------------


def _sides(row: dict[str, Any]) -> tuple[int, int] | None:
    width, height = row.get("width"), row.get("height")
    if (
        isinstance(width, int)
        and isinstance(height, int)
        and not isinstance(width, bool)
        and not isinstance(height, bool)
        and width > 0
        and height > 0
    ):
        return width, height
    return None


def score(width: int, height: int, rules: SelectionRules) -> float:
    size = min(max(width, height), rules.size_cap) / rules.size_cap
    ratio = width / height
    shape = 1 - min(abs(ratio - rules.target_ratio) / rules.target_ratio, 1)
    bonus = rules.landscape_bonus if width >= height else 0.0
    return round(rules.size_weight * size + rules.shape_weight * shape + bonus, 4)


def _dropped(row: dict[str, Any], rules: SelectionRules, rejected: frozenset[str]) -> bool:
    name = normalise_file_name(str(row["commons_file"]))
    if name in rejected or row.get("mime") not in rules.mime_types:
        return True
    if rules.name_pattern.search(name.rsplit(".", 1)[0]):
        return True
    sides = _sides(row)
    return sides is None or sides[0] / sides[1] > rules.max_aspect


def _about_the_subject(
    row: dict[str, Any], rules: SelectionRules, subject_words: frozenset[str]
) -> bool:
    if "wikidata_p18" in row["sources"]:
        return True  # the item's own image
    stem = str(row["commons_file"]).rsplit(".", 1)[0]
    return bool(name_words(stem, rules.category_min_word) & subject_words)


def _tier(row: dict[str, Any], rules: SelectionRules) -> tuple[int, int]:
    if "wikidata_p18" in row["sources"]:
        return (0, 0 if row.get("p18_rank") == "preferred" else 1)
    marks = set(row["quality_marks"])
    for index, name in enumerate(("featured", "quality", "valued"), start=1):
        if marks & rules.tier_marks[name]:
            return (index, 0)
    return (4, 0)


def choose(
    rows: list[dict[str, Any]],
    rules: SelectionRules,
    rejected: frozenset[str],
    subject_words: frozenset[str] | None,
) -> Choice:
    """The main photo among one subject's B5 candidate rows, or why there is none.

    `subject_words` are the name words of the subject itself (see `name_words`); a candidate
    that comes only from a Commons category must share one of them. `None` skips that rule (the
    ranking tests only; the job always passes the subject's words)."""
    if not rows:
        return Choice(None, {"no_candidate"}, 0, False)
    usable = [r for r in rows if r["usable"]]
    if not usable:
        return Choice(None, {"no_usable_candidate"}, len(rows), False)
    survivors = [r for r in usable if not _dropped(r, rules, rejected)]
    if not survivors:
        return Choice(None, {"only_excluded"}, len(rows), False)
    if subject_words is not None:
        survivors = [r for r in survivors if _about_the_subject(r, rules, subject_words)]
        if not survivors:
            return Choice(None, {"only_unrelated_category"}, len(rows), False)

    def long_side(row: dict[str, Any]) -> int:
        return max(_sides(row) or (0, 0))  # survivors always have valid sizes

    passing = [r for r in survivors if long_side(r) >= rules.min_long_side]
    if not passing:
        best = max(long_side(r) for r in survivors)
        return Choice(None, {"only_below_gate"}, len(rows), True, best_long_side=best)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        width, height = _sides(row) or (1, 1)
        return (*_tier(row, rules), -score(width, height, rules), str(row["commons_file"]))

    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(passing, key=key):
        digest = row.get("sha1")
        if isinstance(digest, str) and digest:
            if digest in seen:
                continue  # the same image under another name
            seen.add(digest)
        ranked.append(row)
    chosen = ranked[0]
    width, height = _sides(chosen) or (1, 1)
    flags = set(chosen["flags"])
    portrait = height > width
    if portrait:
        flags.add("portrait")
    if "multiple_p18" in flags and chosen.get("p18_rank") != "preferred":
        flags.add("multiple_p18_chosen_by_score")
    return Choice(
        chosen=chosen,
        flags=flags,
        candidates=len(rows),
        covered=True,
        tier=TIER_NAMES[_tier(chosen, rules)[0]],
        score=score(width, height, rules),
        alternatives=[str(r["commons_file"]) for r in ranked[1 : 1 + ALTERNATIVES]],
        long_side=max(width, height),
        portrait=portrait,
        hero_quality=max(width, height) >= rules.hero_long_side,
    )
