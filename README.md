# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. The skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI), and two jobs work: `discover` and `fetch-listings` (first part). They write local files only; nothing is stored in a database yet. The other jobs come next.

## Licences

- **Code:** MIT, see [LICENSE](LICENSE).
- **Data:** the code's licence does **not** cover the data it fetches. Every row keeps its own source, licence and attribution:
  - Wikivoyage text: CC BY-SA 4.0
  - Wikidata: CC0
  - Wikimedia Commons: the licence of each file (author and licence are stored and shown with every photo)
  - OpenStreetMap: ODbL, "© OpenStreetMap contributors"

## Run it

Python 3.11. In this folder:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m catalogue --help
```

### `discover` and `report`

```text
.venv\Scripts\python -m catalogue discover --dump-date 20261101 --detail IN --highlight-file config/proving_batch.txt
.venv\Scripts\python -m catalogue report --dump-date 20261101 --detail IN --category-total usable=11233
```

`discover` downloads the Wikivoyage dump into `dumps/` (checked against Wikimedia's published SHA-1), finds every destination, assigns each to a country, confirms the country with Wikidata, and writes local files to `out/discover/<dump date>/` (both folders are gitignored). It keeps **every** article, not only the destinations. `report` turns those files into research tables and a plain-language summary. `overrides/country_iso.csv` holds hand-checked country codes, each with a written reason.

**Slug registry:** `data/slugs.json` (Wikivoyage page ID -> slug and latest title) is committed, because `out/` does not survive on GitHub runners and a slug must never change once handed out. `discover` reads it and adds new pages to it (`--data-dir` changes the folder). Changes to it go through a reviewed pull request after each by-hand `discover` wave; unattended jobs never assign slugs. Never delete or regenerate it from scratch: `discover` stops with an error if the file is missing (use `--init-registry` only for a genuine first run). The old `out/discover/slugs.json` from B2 is no longer read and can be deleted.

### `fetch-listings`

```text
.venv\Scripts\python -m catalogue fetch-listings --dump-date 20261101 --detail IN
```

Offline: no network, no database, no settings. It reads the dump again and keeps **every** listing (see, do, buy, eat, drink, sleep, go, listing) and every section text from **every** article, using `out/discover/<dump date>/articles.jsonl` as the page list. It refuses to run unless the dump is the very file `discover` used (SHA-1 compared with the discover manifest). Files go to `out/listings/<dump date>[-CC]/`: `listings.jsonl`, `sections.jsonl`, `markers.jsonl` (map pins, not listings), `manifest.json`, count tables and `report.md`. A district's listings are also attributed to its city or region (`owner_*`, `via_district`). Values are kept raw; prices and hours are interpreted in a later step. The console prints counts only.

Settings come from environment variables (names only in [.env.example](.env.example)); a local `.env` file is read when present. Nothing secret is ever committed.

## Known follow-ups

Small items found in review of `fetch-listings`, not yet done:

- **Memory:** every listing's key inputs are kept in memory until the files are finished. The world run (463K listings) held about 430 MB, so this is fine today; compute keys from the spool file if it ever grows.
- **Reads:** `articles.jsonl` is read twice (parsed, then hashed). Hash it in chunks or in the same pass.
- **Region label in the detail report:** it assumes `discover` lists ancestors nearest first and does not check it; a country page's own listings also get the label "(directly under the country)".
- **Listing keys for non-Latin names (planned for B3b):** keys are ASCII-folded today, so names in other scripts become `kind:unnamed` and rely on their Wikidata, coordinate or address label. B3b will count the affected listings per country and then keep Unicode letters and digits, normalised (NFKC) and case-folded, in the key, with a `key_version` field. This must be settled before B15 loads any key.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
