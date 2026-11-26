"""The summary text shown in the app, made from the stored Wikipedia lead (pure, no I/O).

The stored summary stays exactly as the API returned it (B4b). For display, every bracketed aside
is removed, `( ... )` and `[ ... ]` alike: pronunciations and IPA, "Hindi: ...", unit conversions,
"(as of 2024)" and the empty brackets the extract generator leaves behind. Then the first
`min_sentences` sentences are kept, and further ones up to `max_sentences` while the text stays
within `max_chars`. Removing text is a change to a CC BY-SA work, so the attribution then says
"adapted". Nothing in here is country- or language-specific beyond English punctuation.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from catalogue.errors import PipelineError
from catalogue.publish.hero import CONTENT_PATH

_OPEN, _CLOSE = "([", ")]"
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:.!?])")
_DOUBLE_COMMA = re.compile(r",\s*(?=[,;.])")
_SPACES = re.compile(r"\s+")
# A sentence ends at . ! or ? followed by a space and a capital, a digit or an opening quote.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘A-Z0-9])")
# Words whose full stop does not end a sentence (lower case, without the stop).
_ABBREVIATIONS = frozenset(
    "st mt ft dr mr mrs ms prof gen col lt sgt rev hon jr sr no nos vs etc approx ca c "
    "inc ltd co corp dept est fl sq e.g i.e u.s u.k u.n a.d b.c".split()
)


@dataclass(frozen=True, slots=True)
class SummaryRules:
    min_sentences: int
    max_sentences: int
    max_chars: int


def load_summary_rules(path: Path = CONTENT_PATH) -> SummaryRules:
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))["summary"]
        values = (table["min_sentences"], table["max_sentences"], table["max_chars"])
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise PipelineError(f"{path.name} is missing or damaged") from None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values):
        raise PipelineError(f"{path.name}: [summary] values must be positive whole numbers")
    if values[0] > values[1]:
        raise PipelineError(f"{path.name}: [summary] min_sentences is above max_sentences")
    return SummaryRules(*values)


def strip_brackets(text: str) -> str:
    """The text without its bracketed parts (nested ones too), tidied. A bracket that is never
    closed means the text cannot be read for sure, so it is returned as it was."""
    kept: list[str] = []
    depth = 0
    for char in text:
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE and depth:
            depth -= 1
        elif not depth:
            kept.append(char)
    if depth:
        return text
    tidy = _SPACES.sub(" ", "".join(kept))
    tidy = _SPACE_BEFORE_PUNCT.sub(r"\1", tidy)
    return _DOUBLE_COMMA.sub("", tidy).strip()


def _ends_sentence(before: str) -> bool:
    """Whether the stop that ends `before` closes a sentence (not "St.", "U.S." or "J.")."""
    if not before.endswith("."):
        return True
    word = before.rsplit(" ", 1)[-1][:-1].lower()
    if len(word) == 1 and word.isalpha():
        return False  # an initial
    return word not in _ABBREVIATIONS


def sentences(text: str) -> list[str]:
    found: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        piece = text[start : match.start()]
        if _ends_sentence(piece):
            found.append(piece)
            start = match.end()
    found.append(text[start:])
    return [s for s in found if s]


def display_summary(text: str, rules: SummaryRules) -> str:
    parts = sentences(strip_brackets(text))
    kept = parts[: rules.min_sentences]
    for part in parts[rules.min_sentences : rules.max_sentences]:
        if len(" ".join([*kept, part])) > rules.max_chars:
            break
        kept.append(part)
    return " ".join(kept)


def adapted_attribution(attribution: str, original: str, shown: str) -> str:
    """The attribution, marked "adapted" when bracketed text was taken out of the source. Only
    cutting after a sentence keeps it an excerpt."""
    if _SPACES.sub(" ", original).strip() == strip_brackets(original):
        return attribution
    if attribution.endswith("(excerpt)"):
        return attribution.removesuffix("(excerpt)") + "(excerpt, adapted)"
    return f"{attribution} (adapted)"
