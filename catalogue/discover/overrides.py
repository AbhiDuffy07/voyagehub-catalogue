"""Hand-maintained corrections, kept in the repo and reviewed like code (design: section 5.6).

`overrides/country_iso.csv` gives a country article its ISO 3166-1 code when Wikidata cannot
(for example the Netherlands, whose code Wikidata keeps at deprecated rank on the country item).
An override always wins, and every override needs a written reason.
"""

import csv
import re
from pathlib import Path

from catalogue.discover.parse import normalize_title
from catalogue.errors import PipelineError

_COLUMNS = {"country", "country_iso", "reason"}
_ISO = re.compile(r"^[A-Z]{2}$")


def load_country_iso_overrides(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(lines)
    if set(reader.fieldnames or []) != _COLUMNS:
        raise PipelineError("country_iso overrides need the columns country, country_iso, reason")
    overrides: dict[str, str] = {}
    for row in reader:
        title = normalize_title(row["country"] or "")
        code = (row["country_iso"] or "").strip().upper()
        if not _ISO.match(code):
            raise PipelineError(f"country_iso overrides: {title!r} needs a two-letter code")
        if title in overrides:
            raise PipelineError(f"country_iso overrides: {title!r} appears twice")
        overrides[title] = code
    return overrides
