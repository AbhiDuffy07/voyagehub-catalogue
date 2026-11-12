"""Add the read-in versions of price, hours and lastedit to a listing's raw fields.

The raw values stay in the row as they are; these are extra fields. See `prices.py`, `hours.py`
and `lastedit.py` for what is read and what is deliberately left alone.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from catalogue.listings.hours import parse_hours
from catalogue.listings.lastedit import age_class, parse_lastedit
from catalogue.listings.prices import parse_price
from catalogue.listings.reference import Reference


@dataclass(frozen=True, slots=True)
class Interpreted:
    fields: dict[str, Any]  # the extra row fields
    price_status: str
    hours_status: str
    lastedit_status: str
    age: str  # within, older, unclear or undated, for the two-year numbers


def interpret(
    fields: dict[str, str], *, country_iso: str | None, reference: Reference, dump_day: date
) -> Interpreted:
    price = parse_price(fields["price"], country_iso=country_iso, reference=reference)
    hours = parse_hours(fields["hours"])
    edit = parse_lastedit(fields["lastedit"], dump_date=dump_day)
    extra = {
        "price_status": price.status,
        "price_entries": price.entries,
        "price_is_free": price.is_free,
        "price_tier": price.tier,
        "price_reason": price.reason,
        "hours_status": hours.status,
        "hours_parsed": hours.parsed,
        "lastedit_status": edit.status,
        "lastedit_date": edit.date,
        "lastedit_precision": edit.precision,
        "lastedit_year": edit.year,
    }
    return Interpreted(
        extra, price.status, hours.status, edit.status, age_class(edit, dump_date=dump_day)
    )
