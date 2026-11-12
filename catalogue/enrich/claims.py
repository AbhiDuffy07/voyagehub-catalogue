"""Read the facts B4 needs from a Wikidata entity (`wbgetentities`, props claims/sitelinks/labels).

Unlike the B2 reader in `catalogue/discover/confirm.py`, which only handles item values, this one
understands globe coordinates, quantities and time qualifiers, and never raises on an odd shape:
a value it cannot read is simply absent.

Rank rule (the same as B2): deprecated statements are dropped; if any are preferred, only those
count. Item-valued facts (country, instance of, heritage designation, time zone) also drop a
statement that has an end time (P582), so a former country or a withdrawn designation is not
current. Fees (P2555) keep every statement with its dates, because the dates are what B4 counts.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

COORDINATE = "P625"
COUNTRY = "P17"
INSTANCE_OF = "P31"
HERITAGE = "P1435"
TIME_ZONE = "P421"
IANA_ZONE = "P6687"
IMAGE = "P18"
WHC_ID = "P757"
FEE = "P2555"
CURRENCY_CODE = "P498"
POINT_IN_TIME = "P585"
START_TIME = "P580"
END_TIME = "P582"
PART_OF = "P361"
PART_OF_WORLD_HERITAGE_SITE = "Q43113623"  # "part of UNESCO World Heritage Site"

EARTH = "http://www.wikidata.org/entity/Q2"
_ENTITY_URL = "http://www.wikidata.org/entity/"
_DATE = re.compile(r"[+-](\d{4,})-(\d{2})-(\d{2})T")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True, slots=True)
class Coordinate:
    lat: float
    lon: float
    precision: float | None = None  # degrees; how exactly Wikidata says the point is known


@dataclass(frozen=True, slots=True)
class Fee:
    """One P2555 statement as Wikidata gives it. Nothing is converted or interpreted."""

    amount: str
    unit_qid: str | None  # None for a plain number
    point_in_time: str | None  # YYYY-MM-DD, from P585
    start: str | None  # P580
    end: str | None  # P582


@dataclass(frozen=True, slots=True)
class ItemFacts:
    qid: str
    revision_id: int | None
    label: str | None
    description: str | None
    enwiki_title: str | None
    countries: tuple[str, ...]
    instance_of: tuple[str, ...]
    heritage: tuple[str, ...]
    whs_parents: tuple[str, ...]  # the sites a component is part of (P361 on its designation)
    time_zones: tuple[str, ...]
    iana_zones: tuple[str, ...]
    currency_codes: tuple[str, ...]
    image: str | None
    whc_ids: tuple[str, ...]
    coordinate_status: str  # ok, none, multiple, not_earth, invalid
    coordinate: Coordinate | None
    fees: tuple[Fee, ...]


def _usable(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = [s for s in statements if s.get("rank") != "deprecated"]
    preferred = [s for s in live if s.get("rank") == "preferred"]
    return preferred or live


def _datavalue(statement: Mapping[str, Any]) -> Any:
    snak = statement.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    return snak.get("datavalue", {}).get("value")


def _items(claims: Mapping[str, Any], prop: str) -> tuple[str, ...]:
    found: list[str] = []
    for statement in _usable(claims.get(prop, [])):
        if END_TIME in statement.get("qualifiers", {}):
            continue
        value = _datavalue(statement)
        qid = value.get("id") if isinstance(value, dict) else None
        if isinstance(qid, str) and qid not in found:
            found.append(qid)
    return tuple(found)


def _whs_parents(claims: Mapping[str, Any]) -> tuple[str, ...]:
    """Where a component of a World Heritage Site says which site it belongs to: the P361 ("part
    of") qualifier on its current "part of UNESCO World Heritage Site" designation."""
    found: set[str] = set()
    for statement in _usable(claims.get(HERITAGE, [])):
        if END_TIME in statement.get("qualifiers", {}):
            continue
        value = _datavalue(statement)
        if not isinstance(value, dict) or value.get("id") != PART_OF_WORLD_HERITAGE_SITE:
            continue
        for snak in statement.get("qualifiers", {}).get(PART_OF, []):
            parent = (
                snak.get("datavalue", {}).get("value") if snak.get("snaktype") == "value" else None
            )
            qid = parent.get("id") if isinstance(parent, dict) else None
            if isinstance(qid, str):
                found.add(qid)
    return tuple(sorted(found, key=lambda q: int(q[1:]) if q[1:].isdigit() else 0))


def _strings(claims: Mapping[str, Any], prop: str) -> tuple[str, ...]:
    found: list[str] = []
    for statement in _usable(claims.get(prop, [])):
        value = _datavalue(statement)
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return tuple(found)


def _date(statement: Mapping[str, Any], prop: str) -> str | None:
    """The date of the first qualifier `prop` as YYYY-MM-DD, or None when absent or unreadable."""
    for snak in statement.get("qualifiers", {}).get(prop, []):
        value = snak.get("datavalue", {}).get("value") if snak.get("snaktype") == "value" else None
        match = _DATE.match(value.get("time", "")) if isinstance(value, dict) else None
        if match:
            year, month, day = match.groups()
            return f"{year}-{month}-{day}"
    return None


def _coordinate(claims: Mapping[str, Any]) -> tuple[str, Coordinate | None]:
    values = [v for v in (_datavalue(s) for s in _usable(claims.get(COORDINATE, []))) if v]
    if not values:
        return "none", None
    if len(values) > 1:
        return "multiple", None
    value = values[0]
    if not isinstance(value, dict) or value.get("globe") != EARTH:
        return "not_earth", None
    lat, lon = value.get("latitude"), value.get("longitude")
    numbers = isinstance(lat, int | float) and isinstance(lon, int | float)
    if not numbers or not (math.isfinite(lat) and math.isfinite(lon)):
        return "invalid", None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
        return "invalid", None
    precision = value.get("precision")
    exact = isinstance(precision, int | float) and math.isfinite(precision) and precision > 0
    return "ok", Coordinate(float(lat), float(lon), float(precision) if exact else None)


def _fees(claims: Mapping[str, Any]) -> tuple[Fee, ...]:
    fees = []
    for statement in claims.get(FEE, []):
        if statement.get("rank") == "deprecated":
            continue
        value = _datavalue(statement)
        if not isinstance(value, dict) or not isinstance(value.get("amount"), str):
            continue
        unit = value.get("unit", "")
        unit_qid = unit.removeprefix(_ENTITY_URL) if unit.startswith(_ENTITY_URL) else None
        fees.append(
            Fee(
                amount=value["amount"].lstrip("+"),
                unit_qid=unit_qid,
                point_in_time=_date(statement, POINT_IN_TIME),
                start=_date(statement, START_TIME),
                end=_date(statement, END_TIME),
            )
        )
    return tuple(fees)


def _language_text(entity: Mapping[str, Any], key: str) -> str | None:
    value = entity.get(key, {}).get("en", {}).get("value")
    return value if isinstance(value, str) and value else None


def parse_item(qid: str, entity: Mapping[str, Any]) -> ItemFacts:
    claims = entity.get("claims", {})
    status, coordinate = _coordinate(claims)
    revision = entity.get("lastrevid")
    title = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
    images = _strings(claims, IMAGE)
    return ItemFacts(
        qid=qid,
        revision_id=revision if isinstance(revision, int) else None,
        label=_language_text(entity, "labels"),
        description=_language_text(entity, "descriptions"),
        enwiki_title=title if isinstance(title, str) and title else None,
        countries=_items(claims, COUNTRY),
        instance_of=_items(claims, INSTANCE_OF),
        heritage=_items(claims, HERITAGE),
        whs_parents=_whs_parents(claims),
        time_zones=_items(claims, TIME_ZONE),
        iana_zones=_strings(claims, IANA_ZONE),
        currency_codes=tuple(
            c for c in _strings(claims, CURRENCY_CODE) if _CURRENCY_CODE.fullmatch(c)
        ),
        image=images[0] if len(images) == 1 else None,
        whc_ids=_strings(claims, WHC_ID),
        coordinate_status=status,
        coordinate=coordinate,
        fees=_fees(claims),
    )
