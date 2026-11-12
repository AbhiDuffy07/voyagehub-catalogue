"""One call to a MediaWiki Action API, with the etiquette those servers ask for.

- `maxlag=5`: when the database replicas are behind, the server answers HTTP 200 with an
  `error.code` of `maxlag` and a `Retry-After` header. We wait that long and try again, a bounded
  number of times, and stop with a message instead of hanging.
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
MAX_LAG_RETRIES = 5
DEFAULT_LAG_WAIT = 5.0
MAX_LAG_WAIT = 300.0
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_code(code: object) -> str:
    return _SAFE_CODE.sub("", str(code))[:40] or "unknown"


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
    full = {**params, "format": "json", "maxlag": MAX_LAG}
    for attempt in range(MAX_LAG_RETRIES + 1):
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
        wait = _wait_seconds(response)
        if wait > MAX_LAG_WAIT:
            raise PipelineError(f"{host} asked for a {wait:.0f} s wait (database lag); stopping")
        if attempt == MAX_LAG_RETRIES:
            raise PipelineError(f"{host} is still lagging after retries; try again later")
        sleep(wait)
    raise AssertionError("unreachable: the loop always returns or raises")  # pragma: no cover
