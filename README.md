# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. The skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI), and three jobs work: `discover`, `fetch-listings` and `enrich-wikidata`. They write local files only; nothing is stored in a database yet. The other jobs come next.

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

### `enrich-wikidata`

```text
.venv\Scripts\python -m catalogue enrich-wikidata --dump-date 20261101 --country IN --detail IN
```

Network (Wikivoyage, English Wikipedia and Wikidata Action APIs, through the polite client; needs `CONTACT_EMAIL`). Writes local files only to `out/enrich/<dump date>[-CC]/`. It reads `out/discover/<dump date>/` (always the whole world) and `out/listings/<dump date>[-CC]/`, and refuses to run unless the two belong together (hashes in the fetch-listings manifest). A country run only touches that country's articles, so it is for testing: it cannot see destination items of pages outside the country (one India listing differs from the world run for that reason). Quote country figures from the world run. The console prints counts only.

- **Wikidata IDs:** every article gets one (discover's, else Wikivoyage `pageprops`). A listing with only a `wikipedia=` link is resolved through English Wikipedia (title, `en:` prefix or URL); disambiguation pages, missing pages, section links, other languages and pages without an item are never resolved (`wikipedia_links.csv` lists every outcome).
- **Coordinates:** the page's own Wikivoyage point always wins and is never changed. Wikidata fills only a place with no usable point, and only for exactly one Earth coordinate whose country (P17) matches, that is not a destination's own item, and that is within the page type's `far_km` limit (the same limits as `config/listing_quality.toml`). Otherwise the coordinate stays empty, the Wikidata value is kept beside it, and a flag says why (`wikidata_coord_far`, `wikidata_coord_coarse` (Wikidata's own precision is coarser than `config/enrich_rules.toml` allows), `wikidata_country_disagrees`, `country_unconfirmed`, `item_is_destination`, `multiple_coordinates`, `no_p625`, `not_earth`, `invalid_wikidata_coordinate`, `no_wikidata_id`, `bad_wikidata_id`, `item_missing`, `item_merged`). A `{{geo}}` that is present but invalid counts as no usable point (`own_geo_invalid`). A page type with no distance limit (a region, say) is still filled when the country matches, but the row says `no_far_limit` because it was not distance-checked; a listing on a page with no point of its own says `no_page_point`. A coordinate that comes through a `wikipedia=` link is never filled: it stays as `wikidata_lat`/`wikidata_long` with a `via_link_unverified` flag and is listed in `wikipedia_link_candidates.csv`, for B7 to confirm against OpenStreetMap (a link can name a broad article: a camp's link led to the lake beside it). Where both sources have a point, a distance over `config/enrich_rules.toml` is flagged `wikidata_coord_disagrees`; neither point is changed.
- **UNESCO:** `is_unesco` needs both the World Heritage designation (P1435 = Q9259) and a World Heritage ID (P757); one without the other is flagged `unesco_inconsistent`. A component of a serial site (marked "part of UNESCO World Heritage Site", Q43113623) is kept apart as `unesco_component` and is not `is_unesco`; it carries the parent site's Wikidata ID and English label (`unesco_parent_qid`, `unesco_parent_label`, read from the P361 qualifier on its designation) so the badge can say "Part of UNESCO World Heritage Site: <site>". No parent, several parents, or a parent that is not itself a World Heritage Site is flagged (`unesco_parent_missing`, `unesco_parent_ambiguous`, `unesco_parent_not_whs`). `unesco_components.csv` counts components by parent site. `unesco_badge` is what the app may show: `world_heritage_site` for a site, and `part_of_world_heritage_site` for a component only when its parent site is named and is itself a World Heritage Site on Wikidata. A component without one keeps its flag and gets no badge, because the wording always needs the site's name. All designations are counted by country in `heritage_designations.csv`. The run stops if either Q-ID's label on Wikidata changes.
- **Kind:** `config/kind_map.toml` maps Wikidata classes (P31) to place kinds. A place whose classes map to two kinds gets none and `kind_ambiguous`. `kind_unmapped.csv` lists the commonest classes still unmapped.
- **Time zone (destinations):** the place's own single IANA zone, else the country's single zone labelled `country_single_zone`, else empty with a flag.
- **Entry fees (P2555) are counted, never used (owner ruling, 2026-11-12):** `p2555_counts.csv` and `p2555_samples.csv` show how many attractions have a fee statement, how many are dated and how many are within 2 years. No fee value is in any facts file and Wikidata fees are not used anywhere. On the world run (dump 20261101) only 479 of 58,826 attractions with an item (0.8%) have a fee statement, 20 are dated and 13 are within 2 years; in India 12 of 3,023, none dated, with no audiences. Fees come from official sources only (the ASI list first); the counting stays so the numbers can be watched.
- **Files:** `destination_facts.jsonl` (one row per article), `listing_facts.jsonl` (listings that have an item, or that have no usable point), the tables above, `coord_fill_by_country.csv` and `flags_by_country.csv` (coordinates before and after, by country), `report.md`, `detail-<CC>.md` and `manifest.json`. Every row carries its Wikidata source, revision and CC0 licence. A second run on the same day gives identical files; on another day `read_at` differs, and Wikidata itself may have changed. `destinations.jsonl` is not covered by a manifest hash, so the run only checks that its pages exist in `articles.jsonl`. A reply from Wikidata that leaves out a requested item stops the run instead of flagging those items as missing.

Settings come from environment variables (names only in [.env.example](.env.example)); a local `.env` file is read when present. Nothing secret is ever committed.

### `derive-timezone`

```text
.venv\Scripts\python -m catalogue derive-timezone --dump-date 20261101
```

Offline and fast (seconds): no network, no settings. It reads `out/enrich/<dump date>[-CC]/` (its `destination_facts.jsonl`, checked against that run's manifest and country filter) and writes `out/timezones/<dump date>[-CC]/`. `destinations.timezone` cannot be empty, but after `enrich-wikidata` 14,687 of 24,917 destination rows had no zone (dump 20261101), so B15 waits for this step.

- **Where a zone comes from:** the destination's coordinates (its own point, or one filled from Wikidata), looked up in time-zone boundary data with the `tzfpy` package (exact pin in `requirements.txt`; MIT code, ODbL boundary data inside the wheel, no dependencies). Rows say `timezone_basis: coordinates` and carry the boundary data version.
- **Never a guess:** a place's own Wikidata zone is kept and never replaced (a different offset is only flagged `timezone_disagrees`). A country's single zone, which `enrich-wikidata` uses as a labelled fallback, gives way to the coordinates when they give one clear zone (`timezone_replaced_country_zone` when the offset differs, as when Wikidata names `US/Eastern` as Canada's only zone; `timezone_alias_normalised` when it is only a more exact name for the same offsets, such as `Africa/Asmara` for `Africa/Addis_Ababa`). A point at sea (`Etc/GMT...`), outside every boundary, in two overlapping zones (a disputed border) or with a name the time-zone database does not know gets no zone and a flag (`timezone_no_land_zone`, `timezone_ambiguous`, `timezone_invalid_name`); a Wikidata zone name the time-zone database does not know is not trusted (`timezone_invalid_wikidata_name`); a coordinate that is not a finite number on the globe is `timezone_bad_coordinates` and is never echoed; no coordinate and no Wikidata zone gives `timezone_unknown`. Two names with the same UTC offsets (`Asia/Calcutta`, `Asia/Kolkata`) are not a disagreement.
- **Credit, wherever zones are published:** "Time zones: timezone-boundary-builder, © OpenStreetMap contributors, ODbL". Only zone names are stored (no boundary data), and rows derived from coordinates carry this credit and the licence.
- **Files:** `destination_timezones.jsonl` (one row per destination), `timezones_by_country.csv`, `timezone_disagreements.csv`, `report.md` and `manifest.json` (input hashes, `tzfpy` and data versions, output hashes).

## Known follow-ups

Small items found in review of `fetch-listings`, not yet done:

- **Memory:** every listing's key inputs are kept in memory until the files are finished. The world run (463K listings) held about 430 MB, so this is fine today; compute keys from the spool file if it ever grows.
- **Reads:** `articles.jsonl` is read twice (parsed, then hashed). Hash it in chunks or in the same pass.
- **Region label in the detail report:** it assumes `discover` lists ancestors nearest first and does not check it; a country page's own listings also get the label "(directly under the country)".

- **Memory:** the key and duplicate inputs of every listing also wait in memory until the files are finished (peak memory is in the docs PR numbers).
- **Names with a template:** about 2.6% of India's names are `{{station|...}}` templates; they are flagged `name_markup` and left raw.
- **Coordinates in degrees-minutes-seconds** (`17°21'18"N`) are flagged `coord_invalid`, not converted.

## Progress lines

Long jobs (`discover`, `fetch-listings`, `enrich-wikidata`) print a count-only line at most every 30 seconds and a final `done` line, such as `progress: Wikidata items 12500 of 111984 (11%)`, so a long run can be told from a hung one. A line holds only a fixed label and whole numbers, never a title, row text, address or the User-Agent, and goes to standard output next to the job's summary (standard error stays for errors). The helper is `catalogue/progress.py`.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
