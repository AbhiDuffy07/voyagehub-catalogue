# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. The skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI), and nine jobs work: `discover`, `fetch-listings`, `enrich-wikidata`, `wikipedia-summary`, `derive-timezone`, `photo-candidates`, `photo-select`, `draft-blocks` and `auto-check`. They write local files only; nothing is stored in a database yet. The other jobs come next.

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

### `wikipedia-summary`

```text
.venv\Scripts\python -m catalogue wikipedia-summary --dump-date 20261101 --country IN
```

Network (English Wikipedia Action API, through the polite client; needs `CONTACT_EMAIL`). Writes local files only to `out/summaries/<dump date>[-CC]/`. It reads `out/enrich/<dump date>[-CC]/` and refuses to run unless `destination_facts.jsonl` and `listing_facts.jsonl` match that run's manifest (hashes) and country filter. The subjects are every article, and every see/do listing, for which Wikidata names an English Wikipedia article (`enwiki_title`).

Each distinct title is fetched once, 20 per request (`extracts`, `revisions`, `pageprops`), with up to 3 requests in flight (the polite client's own limit); the output is identical to a serial run and sorted by page and ordinal. The Action API is used because the REST `page/summary` endpoint takes one title per request and is on the way out with RESTBase. A run keeps nothing until the fetch has finished, so after a failure (for example Wikipedia refusing a request) just run it again; a world run is about 3,700 requests.

- **Rows (`summaries.jsonl`):** `lead_text` (the plain-text lead as the API returned it, with surrounding whitespace trimmed), `summary` (its first paragraph, cut at a line break and nothing else), the article title, `url`, a `permalink` to the revision read, `history_url` (where the authors are listed), `revision_id` and timestamp, `licence` (CC BY-SA 4.0) with its link, and an `attribution` that says it is an excerpt. A summary must always be shown with that credit. `attribution` is set only when the row has text; rows without text still carry the target's links, for review. The timestamp is the page's latest revision, which can be newer than the cached extract.
- **Flags, never silent fixes:** `missing_page`, `disambiguation` (no text stored), `empty_extract`, `no_revision` (the page has text but no revision to credit, so the text is held back), `very_short` (shorter than `config/summary_rules.toml` says; the text is kept; world run: 300 of 75,403 leads, 0.4%), `extract_gap` (an empty or dangling bracket such as `Agra (; Hindi: ...)`, left where Wikipedia's extract generator removed a pronunciation template; the text is kept exactly as returned), `redirected` (the text is **held back**: a redirect often leads to a broader or different page, such as a lake's article to its town's; `redirected_candidates.csv` lists them with the target and an excerpt for review), `no_answer` (the reply left the title out, or the title cannot be asked for). A reply of an unexpected shape, or one that never stops continuing, ends the run with a message.
- **Files:** `summaries.jsonl`, `redirected_candidates.csv`, `summaries_by_country.csv`, `flags_by_country.csv`, `report.md` (with the lead-length percentiles the `very_short` limit is checked against) and `manifest.json` (input hashes, rules version, output hashes).

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

### `photo-candidates`

```text
.venv\Scripts\python -m catalogue photo-candidates --dump-date 20261101 --country IN
```

Network (Wikidata and Wikimedia Commons Action APIs, through the polite client; needs `CONTACT_EMAIL`). Writes local files only to `out/photos/<dump date>[-CC]/`. It gathers photo **candidates**; choosing among them is B6. It reads `out/enrich/<dump date>[-CC]/` (hash-checked against that run's manifest and country filter) and `out/listings/<dump date>[-CC]/listings.jsonl` (checked against the manifest hash `enrich-wikidata` recorded), which is the denominator for listing coverage. Photos are hotlinked, never copied. The console prints counts only.

- **Subjects:** every article, and every see/do listing that has a Wikidata item.
- **Source 1, the item's own image (P18):** `enrich-wikidata` stores one image name and drops it when an item has several, so every item without a stored image is re-read from Wikidata (50 items per request, same rank rule as B4: deprecated statements ignored, preferred ones win). Several images: every distinct file becomes a candidate flagged `multiple_p18`, with its rank, and none is chosen. An item that gained a single image since `enrich-wikidata` is flagged `p18_after_facts`.
- **Source 2, Commons quality marks (fallback only):** for subjects with no usable image from source 1, files in the item's Commons category (P373, subcategories included) that carry a Quality, Featured or Valued mark (the Commons categories `Quality images`, `Featured pictures on Wikimedia Commons`, `Valued images sorted by promotion date` and `Valued images`, with file counts checked live in `config/photo_rules.toml`), at most 10 per subject (`category_cap_reached` when there were more; the 10 are chosen by file title among the first 50 search hits). Searching a category is one request and Commons' search is the busiest backend (`cirrussearch-too-busy-error` is waited out five times per category; if a category stays busy, its subjects are flagged `category_search_busy` and the run goes on with everything else, and after three busy categories in a row the rest are flagged without asking. The report and console give the count of busy categories; run the job again to fill them, because a busy flag is not a statement about the category), which is why this step is kept for the subjects that need it. Geosearch (files within 150 m) is not built yet; it waits until after B6.
- **Each candidate** (`photo_candidates.jsonl`) keeps the file, its description page, size, type, SHA-1, the original URL and a thumbnail URL with the width Commons returned (1280 px, a standard Commons width, or the file's own width when it is narrower; other widths come from the original URL at publish time), the author as plain text (markup removed), the licence code, name and link Commons reports, an attribution line ("author, licence, via Wikimedia Commons"), the quality marks, the P18 rank and where it came from (`sources`). Nothing is dropped, rewritten or guessed.
- **Licence allow-list** (`config/photo_rules.toml`, with the reason): CC0, public domain, CC BY and CC BY-SA 2.0 to 4.0 and the Free Art Licence. Others are flagged `licence_not_allowed`, a missing licence `licence_unknown`.
- **Flags:** `multiple_p18`, `p18_after_facts`, `category_cap_reached`, `file_missing`, `licence_not_allowed`, `licence_unknown`, `no_author`, `no_thumb_url`, `not_an_image` (not JPEG, PNG or WebP), and on a subject without a row, `category_search_busy`, `commons_category_unusable` and `multiple_commons_category` (an item naming several Commons categories uses none). Two spellings or a redirect that reach the same file give one row. File titles are sent in batches of at most 50 and at most about 6,000 percent-encoded characters, so a batch of long non-Latin names is split instead of building a URL the servers would refuse. A candidate is **usable** only when it has none of the blocking flags (`file_missing`, `licence_*`, `no_author`, `no_thumb_url`, `not_an_image`). Flagged candidates are kept for review.
- **Coverage** (`report.md`, `coverage_by_country.csv`): the share of destination rows and of see/do listings with at least one usable candidate, with the counts and denominators. Listings without a Wikidata item cannot have a candidate and count against coverage.
- **Files:** `photo_candidates.jsonl`, `photo_candidates_by_country.csv`, `coverage_by_country.csv`, `flags_by_country.csv`, `licences.csv` (files per licence, allowed or not), `report.md` and `manifest.json` (input hashes, rules version, output hashes). A second run on the same day gives identical files unless Commons search was busy in one of them (see `category_search_busy`).

### `photo-select`

```text
.venv\Scripts\python -m catalogue photo-select --dump-date 20261101 --country IN
```

Offline and fast (seconds): no network, no settings. It reads `out/photos/<dump date>[-CC]/` (the `photo-candidates` output, hash-checked against its manifest and country filter) and `out/enrich/<dump date>[-CC]/` (which must be the very facts files `photo-candidates` read), and writes `out/photo_selection/<dump date>[-CC]/`. It picks **one main photo per subject** (every article, and every see/do listing with a facts row) from the usable candidates, by the rules in `config/photo_select.toml` (each with its reason; rows carry `approved_by = "auto:<rules_version>"`).

- **Dropped first:** files in `overrides/rejected_photos.csv` (each with a written reason; an overridden file never comes back; quote a name that contains a comma; names are matched exactly, so `manifest.json` counts rows that matched no candidate in `rejected_files_matching_no_candidate`, which is expected to be non-zero only on a country-filtered run), anything that is not JPEG, PNG or WebP, file names with a whole word from the exclusion list (map, locator, flag, logo, coat of arms, emblem, seal, diagram, banner, chart, schematic), and panoramas wider than 2.5:1. The same image (SHA-1) under another name is kept once, after the gate.
- **Category-only photos must be about the subject:** a candidate that came only from the item's Commons category (not its own P18 image) needs a word of at least four letters in common with the subject's own name (a listing's own name, not its city's). Words that only name a kind of place (fort, temple, palace, lake, national, ...; the list is in `config/photo_select.toml`) never count, and a subject named only with them matches nothing. Names in scripts that split at combining marks (Devanagari, for example) give no words, so only Latin names (Wikidata and Wikipedia titles) can match. Without it the India run chose butterflies, a Nepal peak, a Greenland iceberg and a portrait of a person (`only_unrelated_category`). It is a heuristic: a species photo whose name carries a region word still passes.
- **Size gate:** the long side must be at least 640 px (owner ruling 2026-11-14, after viewing selected photos of 640-1,023 px at card size). Portrait is allowed (tall cards, the app crops) and flagged `portrait`. A candidate below the gate is never chosen, even if it is the only one: the subject is flagged `only_below_gate` and the app uses the city photo. `hero_quality` means a long side of at least 2,000 px.
- **Order:** the Wikidata P18 image (preferred rank first), then Featured, Quality, Valued; the fifth step (unmarked files) is empty until geosearch exists. Inside a tier the score is size (double weight), closeness to 3:2 and a landscape bonus, so landscape and 3:2 outrank portrait at equal size and a much bigger portrait can still win; ties break on the file name. Several normal-rank P18 images are ranked by that score and flagged `multiple_p18_chosen_by_score`.
- **No photo:** `photo: null` and a flag (a subject whose Commons category search was busy in photo-candidates shows as `no_candidate` here, because photo-candidates keeps that flag on the subject, not on a row; check its `category_search_busy` count before reading `no_candidate` as "nothing exists"; `no_candidate`, `no_usable_candidate`, `only_excluded`, `only_unrelated_category`, `only_below_gate`). Never a guess.
- **Report:** coverage (the share of destinations and see/do listings with a selected photo, using photo-candidates' denominators), the tiers, the flags, and **what the size gate costs**: covered subjects (a candidate survived the drops), those the gate removes (`lost_to_gate.csv`) and the percentage. Above 5% the report opens with a warning, because the rules are not frozen until that has been looked at.
- **Files:** `photo_selection.jsonl` (one row per subject: the photo with its whole credit from B5, tier, score, alternatives, flags), `selection_by_country.csv`, `flags_by_country.csv`, `lost_to_gate.csv`, `spot_check_sample.csv` (a seeded sample of 30 rows for review), `report.md` and `manifest.json`. A second run on the same day gives identical files.

### `draft-blocks`

```text
.venv\Scripts\python -m catalogue draft-blocks --dump-date 20261101 --country IN
```

Offline and fast (seconds): no network, no settings. It reads `out/listings/`, `out/enrich/`, `out/photo_selection/` and `out/summaries/` (each hash-checked against its own manifest and country filter) and `out/discover/<dump date>` (the world folder, which must be the very `articles.jsonl` that `fetch-listings` read) and writes `out/blocks/<dump date>[-CC]/`: walkable **areas** and **draft day blocks** (2 to 4 see/do stops close together) for every city. Rules and their reasons: `config/block_rules.toml` and `config/durations.toml` (rows carry `rules_version`; every block has `status = "draft"`: B10 `auto-check` is the gate).

- **Every travel figure is an estimate** (B7 routing is deferred): straight line x 1.3 at 4.5 km/h on foot; a hop over 30 minutes on foot, or the transfer to a far area, uses an assumed 20 km/h by road. Rows say `method = "estimate"` and the block text says "(estimate)".
- **Which stops:** `see` and `do` listings under a city, plus **attraction sub-articles** (below), with a trusted point (the listing's own, or the one `enrich-wikidata` filled), not a duplicate, with a name. Left out and counted by reason (never guessed): a point two sources disagree on, an invalid or link-derived point, **three or more different places with exactly the same point** (a copied coordinate), no point at all, and sections or owners that are not planned.
- **Attraction sub-articles (owner ruling 2026-11-14):** a sight that Wikivoyage keeps as its own article instead of a listing (the Taj Mahal is `Agra/Taj Mahal`) is a stop under its parent city, with its own article coordinates, B4 summary flag and B6 photo (`source = "article"`, `ordinal = 0`, the parent prefix dropped from its name). Whether it is a sight is decided by Wikidata (the kind `enrich-wikidata` gave it: `config/block_rules.toml` `[subarticles]`), not by the Wikivoyage article type, because `Agra/Taj Mahal` is typed `district`. Skipped and counted: sub-articles with no attraction kind (neighbourhoods and districts), types that are destinations or groupings (city, region, rural area, itinerary, travel topic, ...), no point, a disputed point, and one that repeats a listing of the same city (same Wikidata item or name).
- **Areas:** greedy and deterministic. Stops are taken in priority order (UNESCO, has a B6 photo, has known hours, has a known kind, then name); a stop joins the nearest area whose centre is within 1.2 km on foot, else starts a new one.
- **Blocks:** the first stop of a block is the highest-priority unused stop of an area; the block grows by the nearest stop while it stays within about 4 hours (visit midpoints plus walks), no walking hop is over 30 minutes and each stop is open for the slot. An area gets at most two blocks (one morning, one afternoon), because stops this close are often one complex (Agra Fort has about fifteen listings). Every stop is in at most one block.
- **Slot:** from the kind of the first stop (`config/block_rules.toml`; fort, park, viewpoint, religious, sight in the morning; museum, market, neighbourhood in the afternoon); a stop with no kind alternates morning/afternoon (`slot_basis = "alternate"`: either half works, checked against every stop's known hours). `slot_basis` is `kind`, `alternate`, `opposite` (an area's second block takes the other half), `hours` (the slot was flipped because known hours closed the first choice) or `trip`. No evening blocks. No slot is inferred from hours; they only veto one.
- **Hours (light):** a stop is skipped for a slot only when its own parsed hours leave it closed for the whole slot on every day (also for day-trip and paired blocks: a block is moved to the other half, or made a full day, if a stop is closed in its half). A schedule that cannot be read, or a range with equal open and close, counts as unknown hours. Unknown hours never exclude a stop; each block counts them in `hours_unknown_count`. Only about 12% of see/do stops have any hours, so most blocks have several unknown. No time is ever invented.
- **Far areas (day trips):** an area whose centre is over 8 km (straight line) from the city's median stop; far areas within 6 km of each other share one zone and **one** block, with an estimated road transfer from the city centre. It is a `full_day` block only if its visits plus both transfers do not fit half a day. An area so far away that the transfers leave no room for a visit is reported as `too_far_for_day_trip`.
- **Isolated stops:** a stop that cannot make a block of at least two is paired with the nearest other left-over stops within 6 km of its own point (a road hop, flagged `paired_isolated_stops`; paired blocks are not counted against the two-per-area limit); otherwise it is listed in `unblocked_stops.csv` with its reason (`isolated_stop`, `area_block_limit`, `day_trip_area_limit`, `too_far_for_day_trip`, `closed_in_slot`). There are no one-stop blocks.
- **Duration table:** minutes per stop kind (palace 90-120, museum 60-90, temple 30-45 are the design's own; the rest are the author's round numbers, all labelled estimates). A stop with no kind (about two in three) uses the `see` or `do` default and says so (`duration_basis = "default"`). **A UNESCO World Heritage site** (strict: a listed site, not a component) uses one flat longer estimate, 2-3 hours (`[unesco]` in `config/durations.toml`, with its reason; `duration_basis = "unesco"`, and the stop row carries `unesco`).
- **Text is facts only:** the title is "Morning around <first stop>"; the intro lists stop names, about N h, km on foot and the road time, each marked as an estimate. No prices, hours or links.
- **Days they can fill:** a day is a morning block plus an afternoon block, or one full-day block; blocks whose slot only alternated count for either half. No stop repeats.
- **Files:** `areas.jsonl`, `area_travel.jsonl` (estimated travel between nearby area centres), `day_blocks.jsonl`, `day_block_stops.jsonl`, `unblocked_stops.csv`, `city_summary.csv`, `report.md` (counts, what was left out, per city blocks and days), `spot_check_sample.md` (a seeded sample of 20 blocks) and `manifest.json`. A rerun gives identical files.
- **Known gaps (data, not code):** a sub-article with no Wikidata attraction kind is not a stop even if it is a sight; durations come from the kind only (the Taj Mahal is a `sight`, 30-60 min); `see` also holds places that are not sights (stadiums, cinemas, institutes) and two in three stops have no kind, so block quality cannot be told from data alone.

### `auto-check`

```text
.venv\Scripts\python -m catalogue auto-check --dump-date 20261101 --country IN
```

Offline and fast (seconds): no network, no settings. It reads `out/blocks/<dump date>[-CC]/` (the `draft-blocks` output, hash-checked against its manifest and country filter) and writes `out/block_checks/<dump date>[-CC]/`: a **status for every block** (`checked`, `flagged` or `rejected`) with its reasons. Rules and reasons: `config/check_rules.toml` (rows carry `check_rules_version`). It changes nothing in the blocks' files, and **nothing is ever dropped**: a flagged or rejected block keeps all its stops. B12 decides what is served (checked only).

- **Not rebuilt:** `draft-blocks` already guarantees the stop count, walking hops, known hours and no stop reuse. `auto-check` only re-checks, with `draft-blocks`' own rules and from its own output, what it can: stop count outside 2-4, a walking hop over the limit, a half-day block over its time budget, a stop in two blocks (all `rejected`). Hours are not re-checked (they need the listings file).
- **Non-sight stops:** a stop is a non-sight only when its kind is not a sight kind (unknown kind, or neighbourhood), it is not a UNESCO site, its name has none of the sight words (temple, church, museum, fort, ghat, ...), and its name has a whole-word non-sight word (stadium, institute, university, college, school, office, hospital, cinema, theatre, auditorium, golf, club, race course, court, secretariat, factory, hostel, sports, headquarters, bus stand, municipal corporation). The matched word is reported. A block with one non-sight stop and at least two sights left is `flagged` (`non_sight_stop`); a block left with fewer than two sights is `rejected` (`non_sight_stops`).
- **Left off the list on purpose** (they name sights as often as offices): bhavan, centre, complex, research, bank, mall, academy. A wrong flag costs a held-back block, not lost data.
- **Report and files:** `block_checks.jsonl` (one row per block: status, reasons, flagged stops), `city_summary.csv` and `report.md` (per city: blocks by status and **the days the checked blocks can fill** next to the days all draft blocks could fill; totals by reason and by matched word; blocks with no known hours or no photo, for information only), `flagged_sample.md` (a seeded sample of 10 flagged and 10 rejected blocks for the author's own review) and `manifest.json`. A rerun gives identical files.

## Known follow-ups

Small items found in review of `fetch-listings`, not yet done:

- **Memory:** every listing's key inputs are kept in memory until the files are finished. The world run (463K listings) held about 430 MB, so this is fine today; compute keys from the spool file if it ever grows.
- **Reads:** `articles.jsonl` is read twice (parsed, then hashed). Hash it in chunks or in the same pass.
- **Region label in the detail report:** it assumes `discover` lists ancestors nearest first and does not check it; a country page's own listings also get the label "(directly under the country)".

- **Memory:** the key and duplicate inputs of every listing also wait in memory until the files are finished (peak memory is in the docs PR numbers).
- **Names with a template:** about 2.6% of India's names are `{{station|...}}` templates; they are flagged `name_markup` and left raw.
- **Coordinates in degrees-minutes-seconds** (`17°21'18"N`) are flagged `coord_invalid`, not converted.

## Progress lines

Long jobs (`discover`, `fetch-listings`, `enrich-wikidata`, `wikipedia-summary`, `derive-timezone`, `photo-candidates`) print a count-only line at most every 30 seconds and a final `done` line, such as `progress: Wikidata items 12500 of 111984 (11%)`, so a long run can be told from a hung one. A line holds only a fixed label and whole numbers, never a title, row text, address or the User-Agent, and goes to standard output next to the job's summary (standard error stays for errors). The helper is `catalogue/progress.py`.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
