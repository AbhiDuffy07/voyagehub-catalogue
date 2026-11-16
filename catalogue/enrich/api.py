"""One call to a MediaWiki Action API, with the etiquette those servers ask for.

- `maxlag=5`: when the database replicas are behind, the server answers HTTP 200 with an
  `error.code` of `maxlag` and a `Retry-After` header. We wait at least that long (the waits grow
  from 5 s up to 60 s; a longer Retry-After is honoured while it fits the budget) and try again,
  for up to 20 minutes of waiting in total, then stop with a message instead of hanging.
  Unattended runs need that patience: a lag spell on Wikidata can last many minutes. Each wait
  prints one short line (host, lag seconds, lag type and counts only).
- The reply's `error.type` says what lagged. `db` (database replicas, the lag `maxlag` exists to
  protect) always keeps the back-off above. Any other type, such as Wikidata's query-service lag,
  has nothing to do with our read-only GETs, so that one request is retried once without
  `maxlag` and without waiting. A missing or odd type counts as `db` (the polite default). The
  next request starts with `maxlag=5` again.
- Any other `error` object in the body becomes a PipelineError that shows the error code only,
  cleaned of anything but letters, digits, dash and underscore. The server's free-text `info` is
  never printed (the repository is public and so are its logs).
- HTTP-level problems (429, 5xx, timeouts) are already handled by `PoliteClient`.
"""

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from catalogue.errors import PipelineError
from catalogue.http import PoliteClient

MAX_LAG = 5
DEFAULT_LAG_WAIT = 5.0  # first wait, and the wait when the reply has no Retry-After
MAX_LAG_WAIT = 60.0  # the growing back-off never goes above this per wait
MAX_LAG_BUDGET = 1200.0  # seconds of waiting we are willing to spend on one request
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_code(code: object) -> str:
    return _SAFE_CODE.sub("", str(code))[:40] or "unknown"


def _lag_type(error: dict[str, Any]) -> str:
    """What lagged, lower-cased and cleaned. Missing, non-text or empty-after-cleaning is `db`."""
    raw = error.get("type")
    cleaned = _SAFE_CODE.sub("", raw).lower()[:40] if isinstance(raw, str) else ""
    return cleaned or "db"


def _lag_seconds(error: dict[str, Any]) -> str:
    lag = error.get("lag")
    if isinstance(lag, bool) or not isinstance(lag, int | float):
        return ""
    return f"{lag:.1f} s lag, "


def _wait_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "").strip()
    return float(value) if value.isdigit() else DEFAULT_LAG_WAIT


def api_get(
    client: PoliteClient,
    url: str,
    params: Mapping[str, str | int],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GET one Action API request and return the decoded JSON object."""
    host = httpx.URL(url).host
    full: dict[str, str | int] = {**params, "format": "json", "maxlag": MAX_LAG}
    waited = 0.0
    lag_replies = 0
    unthrottled = False
    while True:
        response = client.get(url, params=full)
        try:
            body = response.json()
        except ValueError:
            raise PipelineError(f"{host} did not answer with JSON") from None
        if not isinstance(body, dict):
            raise PipelineError(f"{host} answered with an unexpected JSON shape")
        error = body.get("error")
        if error is None:
            return body
        code = error.get("code") if isinstance(error, dict) else None
        if code != "maxlag":
            raise PipelineError(f"{host} API error: {_safe_code(code)}")
        if unthrottled:
            raise PipelineError(f"{host} is still lagging after retries; try again later")
        lag_type = _lag_type(error)
        detail = f"({_lag_seconds(error)}type {lag_type})"
        if lag_type != "db":
            print(f"{host} is lagging {detail}; retrying once without maxlag", flush=True)
            full = {k: v for k, v in full.items() if k != "maxlag"}
            unthrottled = True
            continue
        asked = _wait_seconds(response)
        wait = max(asked, min(DEFAULT_LAG_WAIT * 2**lag_replies, MAX_LAG_WAIT))
        if waited + wait > MAX_LAG_BUDGET:
            raise PipelineError(f"{host} is still lagging after retries; try again later")
        lag_replies += 1
        print(
            f"{host} is lagging {detail}; waiting {wait:.0f} s "
            f"(retry {lag_replies}, {waited + wait:.0f} of {MAX_LAG_BUDGET:.0f} s used)",
            flush=True,
        )
        sleep(wait)
        waited += wait
