"""auto-check (B10), the pure part: the status of one draft block.

`checked` blocks can be served, `flagged` blocks are held back with reasons, `rejected` blocks are
never served. B8 already guarantees stop count, walking hops, known hours and no reuse, so this
only re-checks (with B8's own rules, from B8's own output) what it can, and adds the non-sight
flag. A flagged or rejected block keeps every stop: nothing is removed or rewritten.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.blocks.rules import BlockRules
from catalogue.errors import PipelineError
from catalogue.photos.select import _reason, _strings, _table

CHECK_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "check_rules.toml"
STATUSES = ("checked", "flagged", "rejected")
# Reasons that reject a block outright (in the order they are reported).
REJECT_REASONS = (
    "stop_count_out_of_range",
    "walk_hop_over_limit",
    "half_day_over_budget",
    "stop_used_twice",
)


@dataclass(frozen=True, slots=True)
class CheckRules:
    version: int
    non_sight_words: tuple[str, ...]
    sight_words: tuple[str, ...]
    sight_kinds: tuple[str, ...]
    reason: str
    non_sight_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    sight_pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str
    failures: list[str]
    flagged: list[dict[str, Any]]  # non-sight stops: position, name, matched word


def _word_pattern(word: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(word.casefold())}s?(?![a-z0-9])")


def load_check_rules(path: Path = CHECK_RULES_PATH) -> CheckRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("check_rules_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PipelineError(f"{source}: check_rules_version needs a whole number of at least 1")
    table = _table(document, "non_sight", source)
    words = tuple(_strings(table, "words", source))
    sights = tuple(_strings(table, "sight_words", source))
    kinds = tuple(_strings(table, "sight_kinds", source))
    if {w.casefold() for w in words} & {w.casefold() for w in sights}:
        raise PipelineError(f"{source}: a word is listed as both non-sight and sight")
    return CheckRules(
        version=version,
        non_sight_words=words,
        sight_words=sights,
        sight_kinds=kinds,
        reason=_reason(table, source, "non_sight"),
        non_sight_patterns=tuple((w, _word_pattern(w)) for w in words),
        sight_pattern=re.compile("|".join(_word_pattern(w).pattern for w in sights)),
    )


def stop_identity(stop: dict[str, Any]) -> tuple:
    """What makes a stop one place: its source row, never its (city-local) listing key."""
    return (stop["source"], stop["page_id"], stop["ordinal"])


def non_sight_word(stop: dict[str, Any], rules: CheckRules) -> str | None:
    """The matched word when the stop is a non-sight, else None."""
    if stop.get("kind") in rules.sight_kinds or stop.get("unesco"):
        return None
    name = str(stop.get("name") or "").casefold()
    if rules.sight_pattern.search(name):
        return None
    for word, pattern in rules.non_sight_patterns:
        if pattern.search(name):
            return word
    return None


def check_block(
    block: dict[str, Any],
    stops: list[dict[str, Any]],
    block_rules: BlockRules,
    rules: CheckRules,
    reused: frozenset[tuple] = frozenset(),
) -> Verdict:
    limits = block_rules.blocks
    failures: list[str] = []
    if not limits.min_stops <= len(stops) <= limits.max_stops:
        failures.append("stop_count_out_of_range")
    if any(
        s["hop_method"] == "walk" and s["hop_minutes"] > block_rules.travel.max_walk_hop_min
        for s in stops
    ):
        failures.append("walk_hop_over_limit")
    if block["slot"] != "full_day" and block["minutes"] > limits.half_day_minutes + 1:
        failures.append("half_day_over_budget")
    if any(stop_identity(s) in reused for s in stops):
        failures.append("stop_used_twice")
    flagged = []
    for stop in stops:
        word = non_sight_word(stop, rules)
        if word is not None:
            flagged.append({"position": stop["position"], "name": stop["name"], "word": word})
    if flagged:
        sights_left = len(stops) - len(flagged)
        failures.append("non_sight_stops" if sights_left < limits.min_stops else "non_sight_stop")
    if any(f in REJECT_REASONS or f == "non_sight_stops" for f in failures):
        status = "rejected"
    elif failures:
        status = "flagged"
    else:
        status = "checked"
    return Verdict(status, failures, flagged)
