"""Read opening hours, but only when there is exactly one reading.

The raw text is always kept in the row. `parse_hours` fills `parsed` only for a small, clear
grammar: day names and ranges (`Mon-Fri`, `Sat-Thu` wraps the week), `closed` days, one or more
time ranges, `noon`, `midnight`, `24/7` and `24 hours`. A time range must be unmistakable:

- 12-hour times need am/pm on both ends. The one exception is a start without am/pm that takes
  the end's marker, accepted only when that makes the start earlier than the end (`3-8PM`).
  `9-5PM` and `10-12PM` would need a guess, so they stay raw.
- 24-hour times need a colon on both ends and a form that cannot be 12-hour (`17:00`, `09:00`).

Seasons, free text, a day without times, two clauses covering the same day, and anything else
give `raw_only`. Nothing is guessed.
"""

import re
from dataclasses import dataclass
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}
_TIME = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s?(?:am|pm)?)"
_RANGE = re.compile(rf"({_TIME})-({_TIME})")
_POINT = re.compile(r"(noon|midnight)|(\d{1,2})(?::(\d{2}))?\s?(am|pm)?")
_ALWAYS = re.compile(
    r"(open )?(24/7|24 ?(hours?|hrs?)( a day| daily| per day| every day)?|always open)"
)
_EVERY_DAY = frozenset({"daily", "every day", "everyday"})

# word (noon, midnight) or None, hour, minute, am/pm or None, written with a colon
_Point = tuple[str | None, int, int, str | None, bool]


@dataclass(frozen=True, slots=True)
class Hours:
    status: str  # empty, parsed, raw_only
    parsed: dict[str, Any] | None


_RAW = Hours("raw_only", None)


class _Ambiguous(Exception):
    """More than one reading, or not in the grammar: keep the raw text only."""


def _normalise(raw: str) -> str:
    text = raw.lower().strip()
    text = text.replace("a.m.", "am").replace("p.m.", "pm").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+to\s+", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s*&\s*|\s+and\s+", ", ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _day(word: str) -> str:
    word = word.strip()
    if word.endswith("s") and word[:-1] in _DAY_NAMES:  # "Mondays"
        word = word[:-1]
    if word not in _DAY_NAMES:
        raise _Ambiguous
    return _DAY_NAMES[word]


def _days(spec: str) -> list[str]:
    spec = spec.strip(" :.,")
    if spec in _EVERY_DAY or not spec:
        return list(DAYS)
    first, _, last = spec.partition("-")
    start = _day(first)
    if not last:
        return [start]
    end = _day(last)
    indexes = [DAYS.index(start)]
    while DAYS[indexes[-1]] != end:
        indexes.append((indexes[-1] + 1) % 7)
    return [DAYS[i] for i in indexes]


def _point(text: str) -> _Point:
    match = _POINT.fullmatch(text)
    if match is None:
        raise _Ambiguous
    if match[1]:
        return match[1], 0, 0, None, False
    return None, int(match[2]), int(match[3] or 0), match[4], match[3] is not None


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _twelve(point: _Point, marker: str | None, *, end: bool) -> int:
    word, hour, minute, _, _ = point
    if word == "noon":
        return 12 * 60
    if word == "midnight":
        return 24 * 60 if end else 0
    if not (1 <= hour <= 12 and minute < 60) or marker is None:
        raise _Ambiguous
    return (hour % 12 + (12 if marker == "pm" else 0)) * 60 + minute


def _twenty_four(point: _Point) -> int:
    _, hour, minute, _, has_colon = point
    if not has_colon or minute >= 60 or hour > 24 or (hour == 24 and minute):
        raise _Ambiguous
    return hour * 60 + minute


def _is_24_hour_form(first: str, last: str) -> bool:
    """`17:00` is plainly 24-hour; so are two-digit hours on both ends (`09:00-12:00`)."""
    hours = [m.group(0) for m in (re.match(r"\d+", first), re.match(r"\d+", last)) if m]
    return len(hours) == 2 and (any(int(h) > 12 for h in hours) or all(len(h) == 2 for h in hours))


def _range(first: str, last: str) -> tuple[list[str], bool]:
    a, b = _point(first), _point(last)
    if a[3] or b[3] or a[0] or b[0]:  # twelve-hour clock
        if a[3] is None and a[0] is None and b[3] is not None:
            # `3-8PM`: the start takes the end's marker, only if that puts it before the end.
            start, finish = _twelve(a, b[3], end=False), _twelve(b, b[3], end=True)
            if not start < finish:
                raise _Ambiguous
        else:
            start, finish = _twelve(a, a[3], end=False), _twelve(b, b[3], end=True)
    else:
        if not _is_24_hour_form(first, last):
            raise _Ambiguous
        start, finish = _twenty_four(a), _twenty_four(b)
    if start == finish:
        raise _Ambiguous
    return [_clock(start), _clock(finish)], finish < start


def _clause(text: str) -> tuple[str, list[str], list[list[str]], bool]:
    """(kind, days, ranges, overnight) for one clause; kind is `closed` or `open`."""
    if "closed" in text:
        remainder = text.replace("closed", "").strip(" :.,")
        if not remainder or re.search(r"\d", remainder):
            raise _Ambiguous  # a bare `closed` could mean anything
        return "closed", _days(remainder), [], False
    matches = list(_RANGE.finditer(text))
    if not matches:
        raise _Ambiguous
    ranges: list[list[str]] = []
    overnight = False
    for match in matches:
        pair, night = _range(match.group(1), match.group(2))
        ranges.append(pair)
        overnight = overnight or night
    return "open", _days(_RANGE.sub(" ", text)), ranges, overnight


def _is_continuation(part: str) -> bool:
    """A comma part with times but no days continues the clause before it."""
    return bool(_RANGE.search(part)) and not _RANGE.sub("", part).strip(" :.,-")


def _clauses(text: str) -> list[str]:
    clauses: list[str] = []
    for segment in text.split(";"):
        for part in (p.strip() for p in segment.split(",")):
            if clauses and _is_continuation(part) and "closed" not in clauses[-1]:
                clauses[-1] += f" {part}"
            else:
                clauses.append(part)
    return clauses


def _result(
    schedule: list[dict[str, Any]], closed: list[str], *, always_open: bool, overnight: bool
) -> dict[str, Any]:
    return {
        "schedule": schedule,
        "closed_days": closed,
        "always_open": always_open,
        "overnight": overnight,
    }


def parse_hours(raw: str) -> Hours:
    text = _normalise(raw)
    if not text:
        return Hours("empty", None)
    if _ALWAYS.fullmatch(text):
        return Hours("parsed", _result([], [], always_open=True, overnight=False))
    schedule: list[dict[str, Any]] = []
    closed: list[str] = []
    seen: set[str] = set()
    overnight = False
    try:
        for clause in _clauses(text):
            kind, days, ranges, night = _clause(clause)
            if seen & set(days):
                raise _Ambiguous  # two readings of the same day
            seen.update(days)
            overnight = overnight or night
            if kind == "closed":
                closed.extend(days)
            else:
                schedule.append({"days": days, "ranges": ranges})
    except _Ambiguous:
        return _RAW
    return Hours("parsed", _result(schedule, closed, always_open=False, overnight=overnight))
