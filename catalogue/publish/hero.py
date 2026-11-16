"""Which photos may be a city's hero (the B6 checks plus a collage and author check), and which
credits any published photo may carry.

B6 chose each photo for its own purpose (a 640 px gate, drop words, an override list). A hero is
shown large at the top of a city, so it must also be hero size (B6 `hero_quality`), must not be a
collage by file name, and must have a real author. Nothing here is country-specific.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError
from catalogue.photos.select import (
    REJECTED_PATH,
    SELECT_RULES_PATH,
    load_rejected_files,
    load_selection_rules,
    normalise_file_name,
)

EMAIL = re.compile(r"\S+@\S+\.\S+")
CONTENT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "publish_content.toml"


@dataclass(frozen=True, slots=True)
class HeroRules:
    name_pattern: re.Pattern[str]  # B6 drop words and the collage words
    rejected: frozenset[str]  # B6 override list
    bad_author: re.Pattern[str]
    max_author_chars: int


def _words(table: dict[str, Any], key: str) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise PipelineError(f"publish_content.toml: [hero] {key} needs a non-empty list of strings")
    return value


def _any_of(words: list[str], *, whole: bool) -> re.Pattern[str]:
    body = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"\b(?:{body})\b" if whole else body, re.IGNORECASE)


def load_hero_rules(
    content_path: Path = CONTENT_PATH,
    select_path: Path = SELECT_RULES_PATH,
    rejected_path: Path = REJECTED_PATH,
) -> HeroRules:
    try:
        hero = tomllib.loads(content_path.read_text(encoding="utf-8"))["hero"]
        collage = _words(hero, "collage_words")
        phrases = _words(hero, "bad_author_phrases")
        max_chars = hero["max_author_chars"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise PipelineError(f"{content_path.name} is missing or damaged") from None
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise PipelineError(f"{content_path.name}: max_author_chars needs a positive whole number")
    select = load_selection_rules(select_path)
    both = f"{select.name_pattern.pattern}|{_any_of(collage, whole=True).pattern}"
    return HeroRules(
        name_pattern=re.compile(both, re.IGNORECASE),
        rejected=load_rejected_files(rejected_path),
        bad_author=_any_of(phrases, whole=False),
        max_author_chars=max_chars,
    )


def hero_problem(row: dict[str, Any], rules: HeroRules) -> str | None:
    """Why a B6 photo row may not be a hero (`size`, `override`, `name`, `author`), else None."""
    photo = row.get("photo") or {}
    if not row.get("hero_quality"):
        return "size"
    name = normalise_file_name(str(photo.get("commons_file") or ""))
    if name in rules.rejected:
        return "override"
    if rules.name_pattern.search(name.rsplit(".", 1)[0]):
        return "name"
    return "author" if author_problem(photo, rules) else None


def author_problem(photo: dict[str, Any], rules: HeroRules) -> bool:
    """True when a photo's credit may not be published: no author name, a credit line that points
    elsewhere or is a pasted caption, an e-mail address (never published) or a very long author.
    Applies to every published photo, not only heroes."""
    author = (photo.get("author") or "").strip()
    credit_text = f"{author} {photo.get('attribution') or ''}"  # both are published
    return (
        not author
        or len(author) > rules.max_author_chars
        or bool(rules.bad_author.search(credit_text))
        or bool(EMAIL.search(credit_text))
    )
