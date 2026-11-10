# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. The skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI), and the first job, `discover`, works. It writes local files only; nothing is stored in a database yet. The other jobs come next.

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

Settings come from environment variables (names only in [.env.example](.env.example)); a local `.env` file is read when present. Nothing secret is ever committed.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
