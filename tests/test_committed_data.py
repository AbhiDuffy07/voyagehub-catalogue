"""Guards on the committed data files, so a bad hand-edit or merge shows up in the PR's CI run."""

import json
import re
from pathlib import Path

SLUGS = Path(__file__).resolve().parent.parent / "data" / "slugs.json"
SLUG_FORMAT = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def test_the_committed_slug_registry_is_well_formed():
    raw = SLUGS.read_bytes()
    document = json.loads(raw.decode("utf-8"))

    assert document["schema_version"] == 1
    entries = document["entries"]
    assert entries
    assert all(key.isdigit() for key in entries)
    slugs = [entry["slug"] for entry in entries.values()]
    assert len(set(slugs)) == len(slugs), "two pages share a slug"
    assert all(SLUG_FORMAT.fullmatch(slug) for slug in slugs)
    assert all(set(entry) == {"slug", "title"} for entry in entries.values())


def test_the_committed_slug_registry_has_unix_line_endings_and_no_email_addresses():
    raw = SLUGS.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert b"@" not in raw  # public repo: titles and slugs only, no personal data


def test_the_committed_monument_fees_load_and_each_row_has_an_official_https_source():
    from datetime import date

    from catalogue.prices.fees import FILE_NAME, load_official_fees

    # the module constant is redirected for every test; this one reads the committed file
    fees_path = SLUGS.parent.parent / "overrides" / FILE_NAME

    fees = load_official_fees(fees_path, today=date(2099, 1, 1))

    assert fees, f"{FILE_NAME} is empty"
    assert all(f.source_url.startswith("https://") and f.date_read for f in fees.values())
    assert b"\r" not in fees_path.read_bytes()
