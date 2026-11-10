# voyagehub-catalogue: rules for Claude Code

## What this is
The catalogue pipeline for VoyageHub. It finds destinations and attractions worldwide from open sources (Wikivoyage, Wikidata, Wikimedia Commons, OpenStreetMap), approves photos and day blocks by calibrated rules (no per-item manual approval), and publishes static JSON to Cloudflare Pages. India is only the rollout order; no code may be country-specific.
The design lives in the private app repo (`docs/architecture/catalogue-pipeline.md`). If this repo and that design disagree, say so and fix one of them in the same change.

## This repo is PUBLIC
- Never commit secrets, `.env` files, keys, tokens, database URLs, or any personal data. No personal email anywhere. Commits use a repo-local noreply identity; do not change it.
- GitHub Actions logs are public. Print only PipelineError messages (safe by construction) and counts. Never print the User-Agent, the database URL, or raw exception messages.
- `ci.yml` runs on pull requests and must never use secrets. No `pull_request_target`. Every action is pinned to a full commit SHA (`python scripts/check_workflows.py` enforces this).

## Hard rules: always ask the owner first
- Adding, removing or upgrading a dependency (exact pins in `requirements.txt`, each at least 14 days old on PyPI).
- `git push`, creating accounts, adding or changing GitHub secrets or repo settings.
- Any Supabase change. This pipeline connects only as `catalogue_importer`; never use an admin URL here.
- Never force-push, rewrite history, or `reset --hard`.

## How we work
1. Tests first (red, then green), then the smallest code that passes. Run lint, format check and tests before saying "done" and report the real output.
2. Work on a branch, one feature per branch. Never commit to `main` directly.
3. Never claim something works without running it. Say what was verified and what was not.
4. Polite fetching: descriptive User-Agent from `CONTACT_EMAIL` (never hard-coded), at most 3 requests at once and 200 a minute, timeouts always, respect `Retry-After`.
5. Never guess data. A missing value stays empty. Every row keeps its source, licence and attribution.

## Commands (Windows, in this folder; Python 3.11, virtual environment in `.venv`)
- First time: `python -m venv .venv`, then `.venv\Scripts\python -m pip install -r requirements.txt`.
- `.venv\Scripts\python -m ruff check .` · `.venv\Scripts\python -m ruff format --check .` · `.venv\Scripts\python -m pytest`
- `.venv\Scripts\python -m catalogue check [--db] [--network]`: checks settings, the TLS database login, and one polite Wikivoyage call.
