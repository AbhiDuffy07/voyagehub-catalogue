"""A small on-disk cache of successful MediaWiki API replies, for `nearby` (B18).

The key is the API URL plus the sorted request parameters (sha256), so the same question asked
again within `max_age_days` is answered from disk: a rerun, or a retry of a failed job, costs the
Wikimedia servers nothing. Only replies `api_get` accepted are stored (errors never), each with the
day it was read. Entries hold the URL, parameters and reply only (no User-Agent, no contact). A
damaged entry is ignored and fetched again. The folder lives under `out/` (gitignored).
"""

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from catalogue.enrich.api import api_get
from catalogue.http import PoliteClient


class ApiCache:
    def __init__(
        self,
        folder: Path | None,
        today: date,
        max_age_days: int,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._folder = folder
        self._oldest = today - timedelta(days=max_age_days)
        self._today = today
        self._sleep = sleep
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _path(self, url: str, params: Mapping[str, str | int]) -> Path | None:
        if self._folder is None:
            return None
        text = json.dumps([url, sorted((k, str(v)) for k, v in params.items())])
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self._folder / key[:2] / f"{key}.json"

    def _read(self, path: Path | None) -> dict[str, Any] | None:
        if path is None or not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            stored = date.fromisoformat(entry["stored"])
            body = entry["body"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if stored < self._oldest or not isinstance(body, dict):
            return None
        return body

    def get(
        self, client: PoliteClient, url: str, params: Mapping[str, str | int]
    ) -> dict[str, Any]:
        body = self.lookup(url, params)
        if body is None:
            body = self.fetch(client, url, params)
            self.store(url, params, body)
        return body

    def lookup(self, url: str, params: Mapping[str, str | int]) -> dict[str, Any] | None:
        """The stored reply to this question, or None (counted as a hit when found)."""
        body = self._read(self._path(url, params))
        if body is not None:
            with self._lock:
                self.hits += 1
        return body

    def fetch(
        self, client: PoliteClient, url: str, params: Mapping[str, str | int]
    ) -> dict[str, Any]:
        """Ask the API (counted as a miss) without storing the reply: for a caller that stores
        its parts under keys of their own."""
        body = api_get(client, url, params, sleep=self._sleep)
        with self._lock:
            self.misses += 1
        return body

    def store(self, url: str, params: Mapping[str, str | int], body: dict[str, Any]) -> None:
        """Keep `body` as the reply to this question (only replies `api_get` accepted)."""
        path = self._path(url, params)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "stored": self._today.isoformat(),
                "url": url,
                "params": dict(sorted((k, str(v)) for k, v in params.items())),
                "body": body,
            }
            temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
