"""A polite HTTP client for Wikimedia and other open-data servers.

Rules (Wikimedia's User-Agent and API rate-limit policies, checked 2026-11-10):
a descriptive User-Agent with a contact address, at most 3 requests at once, at most 200 a minute,
a timeout on every call, and `Retry-After` respected on HTTP 429.

Errors never include the User-Agent, so the contact address cannot leak into logs.
"""

import threading
import time
from collections.abc import Callable, Mapping

import httpx

from catalogue.settings import Settings, user_agent

MAX_CONCURRENT = 3
REQUESTS_PER_MINUTE = 200
MAX_RETRIES = 3
DEFAULT_RETRY_WAIT = 5.0  # Wikimedia: wait at least 5 s when a 429 has no Retry-After
MAX_RETRY_WAIT = 300.0  # a longer ask means "stop for now": end the job, do not hang in CI
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class UpstreamError(Exception):
    """The server could not give a usable answer. `status` is None for network failures."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimitedError(UpstreamError):
    """The server kept saying "slow down", or asked for a wait longer than we will sit through."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status=429)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After. An HTTP-date or junk counts as missing."""
    value = response.headers.get("Retry-After", "").strip()
    return float(value) if value.isdigit() else None


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        *,
        requests_per_minute: int = REQUESTS_PER_MINUTE,
        max_concurrent: int = MAX_CONCURRENT,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(headers={"User-Agent": user_agent(settings)}, timeout=TIMEOUT)
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._interval = 60.0 / requests_per_minute
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._turn_lock = threading.Lock()
        self._next_start = 0.0

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, params: Mapping[str, str | int] | None = None) -> httpx.Response:
        host = httpx.URL(url).host
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            try:
                response = self._send(url, params)
            except httpx.TransportError as exc:
                if last_try:
                    raise UpstreamError(f"no answer from {host}: {type(exc).__name__}") from exc
                self._sleep(self._backoff(attempt))
                continue

            status = response.status_code
            if 200 <= status < 300:
                return response
            if status == 429:
                wait = _retry_after_seconds(response)
                if wait is None:
                    wait = DEFAULT_RETRY_WAIT * 2**attempt
                if wait > MAX_RETRY_WAIT:
                    raise RateLimitedError(f"{host} asked for a {wait:.0f} s wait; stopping")
                if last_try:
                    raise RateLimitedError(f"{host} is still rate limiting after retries")
                self._sleep(wait)
            elif status in RETRYABLE_STATUS and not last_try:
                self._sleep(self._backoff(attempt))
            else:
                raise UpstreamError(f"HTTP {status} from {host}", status=status)
        raise AssertionError("unreachable: the loop always returns or raises")  # pragma: no cover

    def _send(self, url: str, params: Mapping[str, str | int] | None) -> httpx.Response:
        self._wait_for_turn()
        with self._slots:
            return self._client.get(url, params=params)

    def _wait_for_turn(self) -> None:
        """Space request starts by 60 / requests_per_minute seconds, across all threads."""
        with self._turn_lock:
            now = self._clock()
            start = max(now, self._next_start)
            self._next_start = start + self._interval
        if start > now:
            self._sleep(start - now)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return 2.0 * 2**attempt
