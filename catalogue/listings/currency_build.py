"""Build `data/country_currency.csv`: each country's current currency, never guessed.

Facts come from two sources, each fetched by `scripts/build_country_currency.py`:
- Wikidata: the country's currency (P38, current statements only) and the currency's ISO 4217
  code (P498).
- ISO 4217 "list one" (published by SIX, the maintenance agency): the number of minor-unit digits.

A country gets a currency only when Wikidata names exactly one current currency with exactly one
ISO code. Several currencies (Bhutan: ngultrum and rupee), none, or an unreadable code leave the
row blank with a `note` saying why. A code that ISO gives no exponent for is kept without one, so
amounts in it are never converted to minor units.
"""

import re
import xml.etree.ElementTree as ET  # noqa: S405 (a file from the ISO agency, read by the owner)
from collections.abc import Collection, Mapping
from typing import Any

CURRENCY = "P38"
ISO_4217 = "P498"
END_TIME = "P582"
_ALPHA_CODE = re.compile(r"[A-Z]{3}")
COLUMNS = ("country_iso", "currency_code", "currency_qid", "exponent", "note")


def parse_iso_exponents(xml_text: str) -> dict[str, int]:
    """Currency code -> minor-unit digits. Entries without a code or a number (`N.A.`) are left
    out, so funds and metals never get an exponent."""
    root = ET.fromstring(xml_text)  # noqa: S314
    exponents: dict[str, int] = {}
    for entry in root.iter("CcyNtry"):
        code = (entry.findtext("Ccy") or "").strip()
        digits = (entry.findtext("CcyMnrUnts") or "").strip()
        if code and digits.isdigit():
            exponents[code] = int(digits)
    return exponents


def _statements(claims: Mapping[str, Any], prop: str) -> list[dict[str, Any]]:
    live = [s for s in claims.get(prop, []) if s.get("rank") != "deprecated"]
    preferred = [s for s in live if s.get("rank") == "preferred"]
    return preferred or live


def _value(statement: Mapping[str, Any]) -> Any:
    snak = statement.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    value = snak["datavalue"]["value"]
    return value["id"] if isinstance(value, dict) else value


def current_currency_qids(claims: Mapping[str, Any]) -> list[str]:
    """Currency items of statements that have no end time and are not deprecated."""
    found = []
    for statement in _statements(claims, CURRENCY):
        if END_TIME in statement.get("qualifiers", {}):
            continue
        value = _value(statement)
        if value and value not in found:
            found.append(value)
    return found


def iso_code_of(claims: Mapping[str, Any], known_codes: Collection[str] = ()) -> str | None:
    """The currency's three-letter code. The numeric code is ignored; of several letter codes the
    ones in the current ISO list are kept (a withdrawn code such as PLZ drops out). Still more
    than one reading gives None."""
    codes = {
        v
        for v in (_value(s) for s in _statements(claims, ISO_4217))
        if _ALPHA_CODE.fullmatch(str(v))
    }
    current = {c for c in codes if c in known_codes}
    chosen = current or codes
    return next(iter(chosen)) if len(chosen) == 1 else None


def build_rows(
    countries: Mapping[str, str | None],
    country_claims: Mapping[str, Mapping[str, Any]],
    currency_claims: Mapping[str, Mapping[str, Any]],
    exponents: Mapping[str, int],
) -> list[dict[str, Any]]:
    """One row per country (sorted by code). `countries` maps ISO code to Wikidata item."""
    rows = []
    for iso in sorted(countries):
        row: dict[str, Any] = {
            "country_iso": iso,
            "currency_code": "",
            "currency_qid": "",
            "exponent": "",
            "note": "ok",
        }
        qid = countries[iso]
        if not qid or qid not in country_claims:
            row["note"] = "no_wikidata_item"
        else:
            currencies = current_currency_qids(country_claims[qid])
            if not currencies:
                row["note"] = "no_current_currency"
            elif len(currencies) > 1:
                row["note"] = "several_current_currencies"
            else:
                _fill(row, currencies[0], currency_claims, exponents)
        rows.append(row)
    return rows


def _fill(
    row: dict[str, Any],
    currency_qid: str,
    currency_claims: Mapping[str, Mapping[str, Any]],
    exponents: Mapping[str, int],
) -> None:
    code = iso_code_of(currency_claims.get(currency_qid, {}), exponents.keys())
    if code is None:
        row["note"] = "no_iso_code"
        return
    row["currency_code"], row["currency_qid"] = code, currency_qid
    if code in exponents:
        row["exponent"] = exponents[code]
    else:
        row["note"] = "no_iso_exponent"
