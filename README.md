# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. The skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI), and two jobs work: `discover` and `fetch-listings`. They write local files only; nothing is stored in a database yet. The other jobs come next.

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

Offline: no network, no database, no settings. It reads the dump again and keeps **every** listing (see, do, buy, eat, drink, sleep, go, listing) and every section text from **every** article, using `out/discover/<dump date>/articles.jsonl` as the page list. It refuses to run unless the dump is the very file `discover` used (SHA-1 compared with the discover manifest). Files go to `out/listings/<dump date>[-CC]/`: `listings.jsonl`, `sections.jsonl`, `markers.jsonl` (map pins that are not listings), `manifest.json`, count tables, `report.md`, and review files (`prices_unparsed.csv`, `duplicates.csv`, `wikidata_gap.csv`). The console prints counts only.

What each listing row carries (nothing raw is dropped):

- **Where it belongs:** a district's listings are also attributed to its city or region (`owner_*`, `via_district`). Typed markers (`go`, `see`, ...) on city, district and park pages are listings too (`from_marker: true`); other markers stay in `markers.jsonl`.
- **Key:** `listing_key` keeps letters of every script (NFKC, case-folded, long names capped with a hash); `key_version` is 2 (the ASCII keys of the first version were never loaded).
- **Prices:** `price` stays as written; `price_entries` holds tiers (adult, child, student, senior, foreigner, resident), ranges and currencies, only where the text has one clear reading (`price_status`, `price_reason`). A bare number takes the country's currency and says so (`country_default`). `data/country_currency.csv` (built by `scripts/build_country_currency.py` from Wikidata and the ISO 4217 list; blank when a country has several or no current currencies) and `data/demonyms.csv` are the only country-specific knowledge, and they are data, not code.
- **Hours and last edit:** `hours_parsed` only when there is one reading (`3-8PM` yes, `9-5PM` no); `lastedit_*` keeps a year as a year.
- **Quality flags (`quality_flags`):** coordinates missing, invalid, unchecked or far from the page's own point (limits per page type in `config/listing_quality.toml`, each with a written reason); malformed URL, email, name or Wikidata ID; duplicates inside one city (`duplicate_of`, `duplicate_basis`); the same place under two kinds (`same_place_as`). Listings are flagged, never dropped. `coord_gap` groups listings without coordinates by what could fill them in (a Wikidata ID, a Wikipedia link only, neither). Address text is never geocoded.
- `report.md` and `detail-<CC>.md` also show how old the last edit is for priced and timed listings (the numbers behind the two-year rule, which is decided later), the quality counts and the distance percentiles.

Settings come from environment variables (names only in [.env.example](.env.example)); a local `.env` file is read when present. Nothing secret is ever committed.

## Known follow-ups

Small items found in review of `fetch-listings`, not yet done:

- **Memory:** every listing's key inputs are kept in memory until the files are finished. The world run (463K listings) held about 430 MB, so this is fine today; compute keys from the spool file if it ever grows.
- **Reads:** `articles.jsonl` is read twice (parsed, then hashed). Hash it in chunks or in the same pass.
- **Region label in the detail report:** it assumes `discover` lists ancestors nearest first and does not check it; a country page's own listings also get the label "(directly under the country)".

- **Memory:** the key and duplicate inputs of every listing also wait in memory until the files are finished (peak memory is in the docs PR numbers).
- **Names with a template:** about 2.6% of India's names are `{{station|...}}` templates; they are flagged `name_markup` and left raw.
- **Coordinates in degrees-minutes-seconds** (`17°21'18"N`) are flagged `coord_invalid`, not converted.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
