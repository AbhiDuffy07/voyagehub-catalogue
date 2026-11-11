"""Write the `fetch-listings` output files (local, gitignored, under out/).

The rows are the contract with the later loader (B15). Bump `SCHEMA_VERSION` when a field changes
meaning. Every row keeps its source, licence and attribution (CLAUDE.md rule 5). Files are
written to a temporary name and renamed when complete, so a crash never leaves a half file.
"""

import hashlib
import json
import os
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Self

from catalogue.discover.output import ATTRIBUTION, LICENCE, LICENCE_URL, source_url

SCHEMA_VERSION = 1


def provenance(page: Any, *, dump_date: str, read_at: str) -> dict[str, Any]:
    """The source fields every row carries. `page` needs title, revision_id and timestamp."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source_url": source_url(page),
        "revision_id": page.revision_id,
        "revision_timestamp": page.timestamp,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "dump_date": f"{dump_date[:4]}-{dump_date[4:6]}-{dump_date[6:]}",
        "read_at": read_at,
    }


class JsonlWriter:
    """Stream rows to a UTF-8 JSONL file with LF endings; keeps a SHA-256 of what was written."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        self._temporary = path.with_suffix(path.suffix + ".tmp")
        self._hash = hashlib.sha256()
        self._handle: BinaryIO | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._temporary.open("wb")
        return self

    def _open_handle(self) -> BinaryIO:
        if self._handle is None:
            raise RuntimeError("JsonlWriter must be used in a with block")
        return self._handle

    def write(self, row: dict[str, Any]) -> None:
        data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._open_handle().write(data)
        self._hash.update(data)
        self.count += 1

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._open_handle().close()
        if kind is None:
            os.replace(self._temporary, self.path)
        else:
            self._temporary.unlink(missing_ok=True)

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()
