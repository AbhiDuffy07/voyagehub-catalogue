# voyagehub-catalogue

The catalogue pipeline for VoyageHub, a travel itinerary app. It finds tourist destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), checks them with automatic rules, and publishes them as static JSON files. India is the first rollout; nothing in the code is country-specific.

**Status:** early. Only the skeleton exists (settings, a polite Wikimedia client, a TLS database connection, a run log, a CLI and CI). The jobs come next.

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

Settings come from environment variables (names only in [.env.example](.env.example)); a local `.env` file is read when present. Nothing secret is ever committed.

## Politeness towards Wikimedia

Every request carries a descriptive User-Agent with a contact address, at most 3 requests run at once, at most 200 a minute, and `Retry-After` is respected. See Wikimedia's [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
