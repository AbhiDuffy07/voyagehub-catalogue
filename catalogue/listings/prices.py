"""Read a listing's `price` text into tiers, only where the text has one clear reading.

The raw text always stays in the row. `parse_price` returns:
- `status`: `parsed`, `partial` (some entries read, something else not), `label_only` (Free,
  Budget, Mid-range), `unparsed`, or `empty`; `reason` says why when it is not fully parsed;
- `entries`: one per amount (or per explicit "free for ..."), each with
  `audience` (adult, child, student, senior, foreigner, resident, else null), the verbatim
  `audience_label`, a `label` for camera/video/parking/guide fees, the amount as text and in minor
  units, the currency and where it came from (`currency_basis`: `text`, `country_default`,
  `ambiguous`, `none`), a `qualifier` (from, approx) and a `unit` (per night, for 2).

What is never done: a number with an unclear grouping (`1.600`, `2,5`) is not read; a label is not
matched fuzzily (a typo stays a label with no audience); a shared symbol (`$`, `¥`, `Rs`, `kr`) is
read only when it belongs to the country's own currency; a bare number takes the country's
currency but says so (`country_default`), and only when no amount in the text names a currency;
an amount whose currency has no ISO exponent is kept as text, not converted. Country-specific
knowledge (currencies, demonyms) comes from the data files in `reference.py`.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import mwparserfromhell

from catalogue.listings.reference import Reference

_UNAMBIGUOUS_SYMBOLS = {
    "₹": "INR",
    "€": "EUR",
    "₩": "KRW",
    "₪": "ILS",
    "₫": "VND",
    "฿": "THB",
    "₱": "PHP",
    "₺": "TRY",
    "₴": "UAH",
    "₸": "KZT",
    "₦": "NGN",
    "₡": "CRC",
    "₲": "PYG",
    "₭": "LAK",
    "₮": "MNT",
    "₼": "AZN",
    "₾": "GEL",
    "US$": "USD",
    "AU$": "AUD",
    "A$": "AUD",
    "CA$": "CAD",
    "C$": "CAD",
    "NZ$": "NZD",
    "HK$": "HKD",
    "S$": "SGD",
    "R$": "BRL",
    "RM": "MYR",
    "Rp": "IDR",
    "zł": "PLN",
    "Kč": "CZK",
}
# A shared symbol names one of several currencies; it is read only if the country's is among them.
_SHARED_SYMBOLS = {
    "$": frozenset(
        "USD CAD AUD NZD HKD SGD MXN ARS CLP COP UYU DOP CUP BBD BSD BZD BMD BND FJD GYD JMD KYD "
        "LRD NAD SBD SRD TTD TWD XCD ZWL".split()
    ),
    "£": frozenset("GBP EGP LBP SYP GIP FKP SHP SSP".split()),
    "¥": frozenset({"JPY", "CNY"}),
    "₨": frozenset("INR PKR LKR NPR MUR SCR".split()),
    "rs": frozenset("INR PKR LKR NPR MUR SCR".split()),
    "kr": frozenset("SEK NOK DKK ISK".split()),
}
_SYMBOL_ALTERNATIVES = (
    r"US\$|AU\$|A\$|CA\$|C\$|NZ\$|HK\$|S\$|R\$|RM|Rp|zł|Kč"
    r"|[€£¥₹₩₪₫฿₱₺₴₸₦₡₲₭₮₼₾₨$]|(?i:rs)\.?|(?i:kr)\.?"
)
_PREFIX = rf"(?:(?<![A-Za-z])(?:{_SYMBOL_ALTERNATIVES})|(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z]))"
_NUMBER = r"\d+(?:[,.]\d+)*"
_MONEY = re.compile(
    rf"(?P<pre>{_PREFIX})?\s?(?P<a>{_NUMBER})"
    rf"(?:(?:\s?[-–—]\s?|\s+to\s+)(?P<pre2>{_PREFIX})?\s?(?P<b>{_NUMBER}))?"
    rf"(?:\s?(?P<post>(?<![A-Za-z])[A-Z]{{3}}(?![A-Za-z])|₹|€|(?i:kr)\b))?"
)
_FREE = re.compile(
    r"\b(?:free(?: of charge)?|no (?:entry |admission )?(?:fees?|charges?)|gratis|complimentary)\b",
    re.IGNORECASE,
)
_FREE_ONLY = re.compile(
    r"(free( of charge)?( entry| admission)?|no (entry |admission )?(fees?|charges?)|gratis)",
    re.IGNORECASE,
)
_TIERS = {
    "budget": "budget",
    "mid-range": "mid-range",
    "midrange": "mid-range",
    "splurge": "splurge",
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_YEAR = r"(?:19|20)\d{2}"
_MASKS = (
    # full dates first, so that `as of 2020` does not eat the year of `2020-03-04`
    ("date", rf"\b(?:as of\s+)?{_YEAR}-\d{{2}}-\d{{2}}\b"),
    ("date", r"\b(?:as of\s+)?\d{1,2}/\d{1,2}/(?:19|20)?\d{2}\b"),
    (
        "date",
        rf"\b(?:(?:as of|since|updated|checked|in)\s+)?{_MONTH}\.?\s+(?:\d{{1,2}},?\s+)?{_YEAR}\b",
    ),
    ("date", rf"\b(?:as of|since|updated|checked|in)\s+(?:\d{{1,2}}[/.]\d{{1,2}}[/.])?{_YEAR}\b"),
    ("date", rf"\(\s*{_YEAR}\s*\)"),
    ("age", r"\(\s*\d{1,2}\s*[-–]\s*\d{1,2}\s*\)"),
    ("age", r"\bages?\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\b"),
    ("age", r"\b(?:under|over|below|above)\s+\d{1,2}\b(?:\s*(?:years?|yrs?))?"),
    ("age", r"\b\d{1,2}\s*(?:years?|yrs?)\b(?:\s*(?:old|and (?:under|above|older)))?"),
    ("time", r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"),
    (
        "unit",
        r"\b(?:per|each|a)\s+(?:person|people|adult|adults|child|children|night|nights|hour|hours|"
        r"day|days|room|head|vehicle|car|couple|pax|visit|entry|ticket|km|kg)\b"
        r"|/\s?(?:person|night|day|hour|head)\b"
        r"|\bfor\s+\d+(?:\s+(?:people|persons|adults|children|pax|hours|nights|days))?\b",
    ),
    ("measure", r"\b\d+\s*(?:hours?|hrs?|minutes?|mins?|days?|nights?|km|kg|people|persons|pax)\b"),
)
_MASK_PATTERNS = tuple((kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _MASKS)
_SEPARATOR = re.compile(r"[,;\n]|&|\band\b", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")
_AUDIENCE_WORDS = {
    "adult": "adult",
    "adults": "adult",
    "child": "child",
    "children": "child",
    "kid": "child",
    "kids": "child",
    "student": "student",
    "students": "student",
    "senior": "senior",
    "seniors": "senior",
    "elderly": "senior",
    "pensioner": "senior",
    "pensioners": "senior",
    "foreigner": "foreigner",
    "foreigners": "foreigner",
    "foreign": "foreigner",
    "resident": "resident",
    "residents": "resident",
    "local": "resident",
    "locals": "resident",
}
_FEE_LABELS = frozenset({"camera", "video", "parking", "guide"})
_STOP_WORDS = frozenset(
    "entry entrance fee fees ticket tickets price prices admission cost charge from only approx "
    "approximately about around per each for as of and the a an is are to in on at with plus up "
    "starting starts rs inr usd free no citizen citizens national nationals tourist tourists "
    "visitors visitor".split()
)
_QUALIFIERS = (
    ("from", re.compile(r"\b(?:from|starting at|starts at|starting from)\b", re.IGNORECASE)),
    ("approx", re.compile(r"\b(?:approx\.?|approximately|about|around|circa)\b|~", re.IGNORECASE)),
)
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z]{3})\s*\|\s*([\d.,]+)\s*(?:\|[^}]*)?\}\}")
_WESTERN = re.compile(r"\d{1,3}(?:,\d{3})+")
_LAKH = re.compile(r"\d{1,2}(?:,\d{2})+,\d{3}")
_FRACTION = re.compile(r"\d{1,2}")
_PROBLEMS = (
    "ambiguous_number",
    "reversed_range",
    "leftover_digits",
    "ambiguous_label",
    "currency_unknown",
    "no_minor_units",
)


@dataclass(frozen=True, slots=True)
class PriceResult:
    status: str  # empty, parsed, partial, label_only, unparsed
    entries: list[dict[str, Any]]
    is_free: bool
    tier: str | None
    reason: str | None = None


@dataclass(slots=True)
class _Token:
    start: int
    end: int
    free: bool
    entry: dict[str, Any] | None = None
    amounts: tuple[str, str | None] = ("", None)
    currency_tokens: list[str] = field(default_factory=list)
    problem: str | None = None


def _number(text: str) -> Decimal | None:
    """A number with a clear reading, else None (`1.600` and `2,5` are not guessed)."""
    if "," in text and "." in text:
        whole, _, fraction = text.rpartition(".")
        if "," in whole and not (_WESTERN.fullmatch(whole) or _LAKH.fullmatch(whole)):
            return None
        if "," not in whole and not whole.isdigit():
            return None
        if "," in fraction or not _FRACTION.fullmatch(fraction):
            return None
        return Decimal(whole.replace(",", "") + "." + fraction)
    if "," in text:
        if _WESTERN.fullmatch(text) or _LAKH.fullmatch(text):
            return Decimal(text.replace(",", ""))
        return None
    if "." in text:
        whole, _, fraction = text.partition(".")
        if "." in fraction or not _FRACTION.fullmatch(fraction):
            return None
        return Decimal(text)
    return Decimal(text)


def _plain(text: str) -> str:
    text = _TEMPLATE.sub(r"\1 \2", text)
    return mwparserfromhell.parse(text).strip_code(normalize=True, collapse=True).strip()


def _mask(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """The text with dates, ages, times, measures and units blanked out (same length, so positions
    still match), and the unit spans found. Amounts and labels are read from the blanked text."""
    chars = list(text)
    units: list[tuple[int, int, str]] = []
    for kind, pattern in _MASK_PATTERNS:
        for match in pattern.finditer("".join(chars)):
            start, end = match.span()
            if kind == "unit":
                units.append((start, end, _unit_text(text[start:end])))
            chars[start:end] = " " * (end - start)
    return "".join(chars), sorted(units)


def _unit_text(raw: str) -> str:
    words = " ".join(raw.lower().replace("/", " per ").split())
    return re.sub(r"^(?:a|each) ", "per ", words)


def _resolve(token: str, country_currency: str | None, known: set[str]) -> str | None:
    """The ISO code a currency token stands for, or None when it cannot be told for sure."""
    if token in _UNAMBIGUOUS_SYMBOLS:
        return _UNAMBIGUOUS_SYMBOLS[token]
    key = token.lower().rstrip(".")
    if key in _SHARED_SYMBOLS:
        return country_currency if country_currency in _SHARED_SYMBOLS[key] else None
    return token if token in known else None


def _minor(amount: Decimal, exponent: int | None) -> int | None:
    if exponent is None:
        return None
    scaled = amount * (10**exponent)
    return int(scaled) if scaled == scaled.to_integral_value() else None


def _text(amount: Decimal) -> str:
    return format(amount.normalize(), "f")


def _classify(text: str, country: str | None, reference: Reference) -> dict[str, Any]:
    words = _WORD.findall(text)
    demonyms = reference.demonyms.get(country or "", frozenset())
    audiences: dict[str, str] = {}
    labels: list[str] = []
    residual: list[str] = []
    for word in words:
        lower = word.lower()
        singular = lower[:-1] if lower.endswith("s") else lower
        if lower in _AUDIENCE_WORDS:
            audiences.setdefault(_AUDIENCE_WORDS[lower], word)
        elif lower in demonyms or singular in demonyms:
            audiences.setdefault("resident", word)
        elif lower in _FEE_LABELS:
            labels.append(lower)
        elif lower not in _STOP_WORDS:
            residual.append(word)
    found = {
        "audience": next(iter(audiences)) if len(audiences) == 1 else None,
        "audience_label": None,
        "label": labels[0] if labels else None,
    }
    if audiences:
        found["audience_label"] = " ".join(audiences.values())
    elif 0 < len(residual) <= 3:
        found["audience_label"] = " ".join(residual)
    return found


def _qualifier(prefix: str) -> str | None:
    for name, pattern in _QUALIFIERS:
        if pattern.search(prefix):
            return name
    return None


def _empty_entry() -> dict[str, Any]:
    return {
        "audience": None,
        "audience_label": None,
        "label": None,
        "amount": None,
        "amount_min": None,
        "amount_max": None,
        "amount_minor": None,
        "amount_min_minor": None,
        "amount_max_minor": None,
        "currency": None,
        "currency_basis": None,
        "currency_ambiguous": False,
        "qualifier": None,
        "unit": None,
        "free": False,
    }


def _money_tokens(scan: str, problems: list[str]) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _MONEY.finditer(scan):
        token = _Token(match.start(), match.end(), False)
        token.currency_tokens = [t for t in (match["pre"], match["pre2"], match["post"]) if t]
        low = _number(match["a"])
        high = _number(match["b"]) if match["b"] else None
        if low is None or (match["b"] and high is None):
            token.problem = "ambiguous_number"
        elif high is not None and high < low:
            token.problem = "reversed_range"
        token.amounts = (match["a"], match["b"])
        tokens.append(token)
    return tokens


def _free_tokens(scan: str, money: list[_Token]) -> list[_Token]:
    taken = [(t.start, t.end) for t in money]
    return [
        _Token(m.start(), m.end(), True)
        for m in _FREE.finditer(scan)
        if not any(s < m.end() and m.start() < e for s, e in taken)
    ]


def _fill_amounts(
    entry: dict[str, Any], token: _Token, country: str | None, reference: Reference, explicit: bool
) -> str | None:
    """Fill amount and currency fields; returns a problem name when something stays unread."""
    low = _number(token.amounts[0])
    high = _number(token.amounts[1]) if token.amounts[1] else None
    assert low is not None  # noqa: S101 (problem tokens never get here)
    country_info = reference.country_currency.get(country or "")
    country_code = country_info.code if country_info else None
    codes = {_resolve(t, country_code, reference.known_codes) for t in token.currency_tokens}
    if token.currency_tokens:
        entry["currency_basis"] = "text"
        if len(codes) == 1 and None not in codes:
            entry["currency"] = codes.pop()
        else:
            entry["currency_ambiguous"] = True
            entry["currency_basis"] = "ambiguous"
    elif country_code is not None and not explicit:
        entry["currency"], entry["currency_basis"] = country_code, "country_default"
    else:
        entry["currency_basis"] = "none"
    exponent = reference.exponents.get(entry["currency"]) if entry["currency"] else None
    if high is None:
        entry["amount"] = _text(low)
        entry["amount_minor"] = _minor(low, exponent)
        converted = entry["amount_minor"] is not None
    else:
        entry["amount_min"], entry["amount_max"] = _text(low), _text(high)
        entry["amount_min_minor"], entry["amount_max_minor"] = (
            _minor(low, exponent),
            _minor(high, exponent),
        )
        converted = entry["amount_min_minor"] is not None and entry["amount_max_minor"] is not None
    if entry["currency"] is None:
        return "currency_unknown"
    return None if converted else "no_minor_units"


def _pieces(
    view: str, tokens: list[_Token]
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[bool]]:
    """For each token, the text that may label it before and after the amount, as offsets, and
    whether the text between it and the previous token could belong to either (no separator)."""
    before: list[tuple[int, int]] = []
    after: list[tuple[int, int]] = []
    unclear: list[bool] = []
    for i, token in enumerate(tokens):
        region_start = tokens[i - 1].end if i else 0
        region = view[region_start : token.start]
        separator = _SEPARATOR.search(region) if i else None
        if separator:
            before.append((region_start + separator.end(), token.start))
            if i:
                after[i - 1] = (after[i - 1][0], region_start + separator.start())
            unclear.append(False)
        else:
            before.append((region_start, token.start))
            unclear.append(bool(i) and bool(region.strip(" :.()-")))
        after.append((token.end, token.end))
        if i == len(tokens) - 1:
            tail = view[token.end :]
            cut = _SEPARATOR.search(tail)
            after[i] = (token.end, token.end + (cut.start() if cut else len(tail)))
    return before, after, unclear


def _has_label(text: str, country: str | None, reference: Reference) -> bool:
    found = _classify(text, country, reference)
    return any(found[k] for k in ("audience", "audience_label", "label"))


def _build_entries(
    view: str,
    tokens: list[_Token],
    units: list[tuple[int, int, str]],
    country: str | None,
    reference: Reference,
    explicit: bool,
    problems: list[str],
) -> list[dict[str, Any]]:
    before, after, unclear = _pieces(view, tokens)
    entries: list[dict[str, Any]] = []
    built: list[tuple[int, dict[str, Any]]] = []
    for i, token in enumerate(tokens):
        prefix, suffix = view[slice(*before[i])], view[slice(*after[i])]
        if unclear[i] and _has_label(prefix, country, reference):
            previous = view[slice(*before[i - 1])]
            if not _has_label(previous, country, reference):
                # `₹10 Child ₹5`: the word may belong to either amount, so neither gets it.
                problems.append("ambiguous_label")
                prefix = ""
        if token.problem is not None:
            problems.append(token.problem)
            continue
        entry = _empty_entry()
        found = _classify(f"{prefix} {suffix}", country, reference)
        entry.update(found)
        entry["qualifier"] = _qualifier(prefix)
        if token.free:
            entry.update(amount="0", amount_minor=0, free=True)
        else:
            problem = _fill_amounts(entry, token, country, reference, explicit)
            if problem:
                problems.append(problem)
        built.append((i, entry))
        entries.append(entry)
    _attach_units(built, tokens, units)
    return entries


def _attach_units(
    built: list[tuple[int, dict[str, Any]]], tokens: list[_Token], units: list[tuple[int, int, str]]
) -> None:
    """A unit belongs to the amount before it (`₹100 per person`), else the one after it."""
    for start, _, unit in units:
        owners = [e for i, e in built if tokens[i].end <= start]
        owner = owners[-1] if owners else (built[0][1] if built else None)
        if owner is not None and owner["unit"] is None:
            owner["unit"] = unit


def _label_only(plain: str) -> PriceResult | None:
    bare = plain.strip(" .;,!")
    if _FREE_ONLY.fullmatch(bare):
        return PriceResult("label_only", [], True, None)
    tier = _TIERS.get(bare.lower().replace(" ", "-"))
    return PriceResult("label_only", [], False, tier) if tier else None


def parse_price(raw: str, *, country_iso: str | None, reference: Reference) -> PriceResult:
    text = _plain(raw)
    if not text:
        return PriceResult("empty", [], False, None)
    scan, units = _mask(text)
    view = scan
    if not re.search(r"\d", scan):
        label_only = _label_only(scan)
        if label_only:
            return label_only
        if not _FREE.search(scan):
            return PriceResult("unparsed", [], False, None, "no_amount")
    problems: list[str] = []
    money = _money_tokens(scan, problems)
    explicit = any(t.currency_tokens for t in money)
    if explicit:
        # With a named currency in the text, a bare number elsewhere is not an amount.
        bare = [t for t in money if not t.currency_tokens]
        money = [t for t in money if t.currency_tokens]
        if bare:
            problems.append("leftover_digits")
    tokens = sorted([*money, *_free_tokens(scan, money)], key=lambda t: t.start)
    entries = _build_entries(view, tokens, units, country_iso, reference, explicit, problems)
    covered = list(scan)
    for token in tokens:
        covered[token.start : token.end] = " " * (token.end - token.start)
    if re.search(r"\d", "".join(covered)) and "leftover_digits" not in problems:
        problems.append("leftover_digits")
    reason = next((p for p in _PROBLEMS if p in problems), None)
    is_free = any(e["free"] and e["audience"] is None for e in entries)
    if not entries:
        return PriceResult("unparsed", [], False, None, reason or "unrecognised")
    return PriceResult("partial" if reason else "parsed", entries, is_free, None, reason)
