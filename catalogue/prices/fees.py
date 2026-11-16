"""`overrides/monument_fees.csv`: official entry fees read by hand from the official page.

A Wikivoyage price may be old or wrong; where the owner of a monument publishes its own fee page,
that figure is used instead, as an exact price. Every row names the city (`owner_slug`) and the
listing it replaces (`listing_key`), the currency, the resident and foreigner fee in whole
currency units, the official page (`source_url`, https) and the day it was read (`date_read`).
A row is never filled from memory, and a place whose official sources disagree gets no row.
Nothing here is country-specific. Error messages name the file only (the logs are public).
"""

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from catalogue.errors import PipelineError

FEES_PATH = Path(__file__).resolve().parent.parent.parent / "overrides" / "monument_fees.csv"
FILE_NAME = "monument_fees.csv"
COLUMNS = (
    "owner_slug",
    "listing_key",
    "place",
    "currency",
    "resident_amount",
    "foreigner_amount",
    "source_url",
    "date_read",
    "note",
)
_CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True, slots=True)
class OfficialFee:
    owner_slug: str
    listing_key: str
    place: str
    currency: str
    resident: str  # whole currency units, as written
    foreigner: str
    source_url: str
    date_read: date
    note: str


def _fail(why: str) -> PipelineError:
    return PipelineError(f"{FILE_NAME}: {why}")


def _amount(text: str, column: str) -> str:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _fail(f"{column} is not a number") from None
    if not value.is_finite() or value < 0:
        raise _fail(f"{column} must be zero or more")
    return text


def _row(raw: dict[str, str], today: date) -> OfficialFee:
    owner, key = raw["owner_slug"].strip(), raw["listing_key"].strip()
    if not owner or not key:
        raise _fail("every row needs owner_slug and listing_key")
    currency = raw["currency"].strip()
    if not _CURRENCY.fullmatch(currency):
        raise _fail("currency must be a 3-letter upper-case code")
    url = raw["source_url"].strip()
    if not url.startswith("https://"):
        raise _fail("every row needs an https source_url (the official page)")
    try:
        read_on = date.fromisoformat(raw["date_read"].strip())
    except ValueError:
        raise _fail("every row needs date_read as YYYY-MM-DD") from None
    if read_on > today:
        raise _fail("date_read is in the future")
    return OfficialFee(
        owner_slug=owner,
        listing_key=key,
        place=raw["place"].strip(),
        currency=currency,
        resident=_amount(raw["resident_amount"].strip(), "resident_amount"),
        foreigner=_amount(raw["foreigner_amount"].strip(), "foreigner_amount"),
        source_url=url,
        date_read=read_on,
        note=raw["note"].strip(),
    )


def load_official_fees(
    path: Path = FEES_PATH, *, today: date
) -> dict[tuple[str, str], OfficialFee]:
    if not path.is_file():
        return {}
    lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(lines)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise _fail("needs the columns " + ", ".join(COLUMNS))
    fees: dict[tuple[str, str], OfficialFee] = {}
    for raw in reader:
        if raw.get(None) or any(raw[c] is None for c in COLUMNS):
            raise _fail("a line has the wrong number of columns (quote text that has a comma)")
        fee = _row(raw, today)
        key = (fee.owner_slug, fee.listing_key)
        if key in fees:
            raise _fail("lists a place twice")
        fees[key] = fee
    return fees
