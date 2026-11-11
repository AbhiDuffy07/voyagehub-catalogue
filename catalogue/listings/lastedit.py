"""Read the `lastedit` of a listing (the date someone last checked it).

Only well-formed ISO values (`YYYY-MM-DD`, `YYYY-MM`, `YYYY`) are read. Anything else stays
unparsed and the raw text is kept in the row; nothing is guessed. A date before Wikivoyage's
listings existed (2003) or after the dump is `implausible`. A year or month keeps its coarse
precision, so a year is never turned into a day.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date

EARLIEST_YEAR = 2003
FRESH_YEARS = 2
_FORMAT = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


@dataclass(frozen=True, slots=True)
class LastEdit:
    status: str  # missing, parsed, unparsed, implausible
    date: str | None  # as written, at its own precision
    precision: str | None  # day, month, year
    year: int | None


def _period(text: str, precision: str) -> tuple[date, date]:
    parts = [int(p) for p in text.split("-")]
    year = parts[0]
    if precision == "year":
        return date(year, 1, 1), date(year, 12, 31)
    month = parts[1]
    if precision == "month":
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    day = date(year, month, parts[2])
    return day, day


def _bounds(raw: str) -> tuple[str, str, date, date] | None:
    """(text, precision, first day, last day) of a well-formed, real date; else None."""
    match = _FORMAT.fullmatch(raw)
    if match is None:
        return None
    precision = "year" if match[2] is None else "month" if match[3] is None else "day"
    try:
        first, last = _period(raw, precision)
    except ValueError:  # month 13, February 30
        return None
    return raw, precision, first, last


def parse_lastedit(raw: str, *, dump_date: date) -> LastEdit:
    text = raw.strip()
    if not text:
        return LastEdit("missing", None, None, None)
    bounds = _bounds(text)
    if bounds is None:
        return LastEdit("unparsed", None, None, None)
    text, precision, first, _ = bounds
    if first.year < EARLIEST_YEAR or first > dump_date:
        return LastEdit("implausible", None, None, None)
    return LastEdit("parsed", text, precision, first.year)


def _cutoff(dump_date: date) -> date:
    year = dump_date.year - FRESH_YEARS
    day = min(dump_date.day, calendar.monthrange(year, dump_date.month)[1])
    return date(year, dump_date.month, day)


def age_class(edit: LastEdit, *, dump_date: date) -> str:
    """`within` or `older` than two years before the dump, `unclear` when the value's period
    straddles the cutoff, `undated` when there is no usable date. Used for reports only."""
    if edit.status != "parsed" or edit.date is None or edit.precision is None:
        return "undated"
    first, last = _period(edit.date, edit.precision)
    cutoff = _cutoff(dump_date)
    if first >= cutoff:
        return "within"
    if last < cutoff:
        return "older"
    return "unclear"
