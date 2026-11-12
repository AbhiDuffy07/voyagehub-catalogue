"""The committed reference data that prices are read against.

- `data/country_currency.csv`: a country's current currency and its ISO 4217 minor-unit digits
  (built by `scripts/build_country_currency.py`; blank when not certain).
- `data/demonyms.csv`: words that mean "a resident of this country" (`IN,indian`), so that a
  price label such as "Indian" is read as a resident price only on a page of that country.

Both are plain CSV files reviewed in pull requests; the code has no country in it.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from catalogue.errors import PipelineError

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
COUNTRY_CURRENCY_PATH = DATA_DIR / "country_currency.csv"
DEMONYMS_PATH = DATA_DIR / "demonyms.csv"
_CODE = re.compile(r"[A-Z]{3}")
_ALLOWED_EXPONENTS = frozenset({0, 1, 2, 3, 4})


@dataclass(frozen=True, slots=True)
class CurrencyInfo:
    code: str
    exponent: int | None  # None: ISO gives none, so amounts are not converted to minor units


@dataclass(frozen=True, slots=True)
class Reference:
    country_currency: dict[str, CurrencyInfo] = field(default_factory=dict)
    demonyms: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def exponents(self) -> dict[str, int]:
        return {
            c.code: c.exponent for c in self.country_currency.values() if c.exponent is not None
        }

    @property
    def known_codes(self) -> set[str]:
        return {c.code for c in self.country_currency.values()}


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not set(columns) <= set(reader.fieldnames):
                raise PipelineError(f"{path.name} must have the columns {', '.join(columns)}")
            return list(reader)
    except OSError:
        raise PipelineError(f"{path.name} could not be read") from None


def load_country_currency(path: Path = COUNTRY_CURRENCY_PATH) -> dict[str, CurrencyInfo]:
    table: dict[str, CurrencyInfo] = {}
    for row in _rows(path, ("country_iso", "currency_code", "exponent")):
        if None in (row["country_iso"], row["currency_code"], row["exponent"]):
            raise PipelineError(f"{path.name} has a row with missing columns")
        code = row["currency_code"].strip()
        if not code:
            continue  # blank on purpose: several currencies, or none certain
        iso = row["country_iso"].strip().upper()
        if not _CODE.fullmatch(code):
            raise PipelineError(f"{path.name}: {iso} has a malformed currency code")
        digits = row["exponent"].strip()
        if digits and (not digits.isdigit() or int(digits) not in _ALLOWED_EXPONENTS):
            raise PipelineError(f"{path.name}: {iso} has an unexpected exponent")
        if iso in table:
            raise PipelineError(f"{path.name}: {iso} appears twice")
        table[iso] = CurrencyInfo(code, int(digits) if digits else None)
    return table


def load_demonyms(path: Path = DEMONYMS_PATH) -> dict[str, frozenset[str]]:
    words: dict[str, set[str]] = {}
    for row in _rows(path, ("country_iso", "demonym")):
        if None in (row["country_iso"], row["demonym"]):
            raise PipelineError(f"{path.name} has a row with missing columns")
        word = row["demonym"].strip().lower()
        if word:
            words.setdefault(row["country_iso"].strip().upper(), set()).add(word)
    return {iso: frozenset(found) for iso, found in words.items()}


def load_reference(
    currency_path: Path = COUNTRY_CURRENCY_PATH, demonyms_path: Path = DEMONYMS_PATH
) -> Reference:
    return Reference(load_country_currency(currency_path), load_demonyms(demonyms_path))
