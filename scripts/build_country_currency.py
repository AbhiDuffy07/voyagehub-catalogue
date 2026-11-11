"""Build data/country_currency.csv from Wikidata and the ISO 4217 list. Run by hand, by the owner.

    python scripts/build_country_currency.py --iso-list <list-one.xml> [--articles <articles.jsonl>]

- `--iso-list` is "list one" from the ISO 4217 maintenance agency (SIX), downloaded by hand.
- `--articles` is the `discover` output; countries and their Wikidata items come from it.
- Wikidata is read with the project's polite client (descriptive User-Agent from CONTACT_EMAIL,
  at most 3 requests at once, 200 a minute). Only counts are printed, never the User-Agent.

The result is reviewed in a pull request before it is committed. Nothing is guessed: see
catalogue/listings/currency_build.py for when a row is left blank.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from catalogue.discover.output import _csv_text, write_text  # noqa: E402
from catalogue.errors import PipelineError  # noqa: E402
from catalogue.http import PoliteClient  # noqa: E402
from catalogue.listings.currency_build import (  # noqa: E402
    COLUMNS,
    build_rows,
    current_currency_qids,
    parse_iso_exponents,
)
from catalogue.settings import load_settings  # noqa: E402

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BATCH = 50


def _countries(articles: Path) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for line in articles.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["type"] == "country" and row["tree_country_iso"]:
            found[row["tree_country_iso"]] = row.get("wikidata_id") or None
    return found


def _claims(client: PoliteClient, qids: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    unique = list(dict.fromkeys(qids))
    for start in range(0, len(unique), BATCH):
        reply = client.get(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(unique[start : start + BATCH]),
                "props": "claims",
                "format": "json",
            },
        ).json()
        for qid, entity in reply["entities"].items():
            if "missing" not in entity:
                found[qid] = entity.get("claims", {})
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iso-list", type=Path, required=True)
    parser.add_argument(
        "--articles", type=Path, default=Path("out/discover/20261101/articles.jsonl")
    )
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "country_currency.csv")
    args = parser.parse_args()

    try:
        exponents = parse_iso_exponents(args.iso_list.read_text(encoding="utf-8"))
        countries = _countries(args.articles)
        client = PoliteClient(load_settings())
        try:
            country_claims = _claims(client, [q for q in countries.values() if q])
            currency_qids = [
                q for claims in country_claims.values() for q in current_currency_qids(claims)
            ]
            currency_claims = _claims(client, currency_qids)
        finally:
            client.close()
    except PipelineError as error:
        print(f"error: {error}")
        return 1

    rows = build_rows(countries, country_claims, currency_claims, exponents)
    write_text(args.out, _csv_text(COLUMNS, [[r[c] for c in COLUMNS] for r in rows]))
    notes: dict[str, int] = {}
    for row in rows:
        notes[row["note"]] = notes.get(row["note"], 0) + 1
    print(f"countries: {len(rows)}; ISO exponents read: {len(exponents)}")
    print("; ".join(f"{k}: {v}" for k, v in sorted(notes.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
