"""Write the `fetch-listings` output files (local, gitignored, under out/).

The rows are the contract with the later loader (B15). Bump `SCHEMA_VERSION` when a field changes
meaning. Every row keeps its source, licence and attribution (CLAUDE.md rule 5). Files are
written to a temporary name and renamed when complete, so a crash never leaves a half file.
"""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Self

from catalogue.discover.output import ATTRIBUTION, LICENCE, LICENCE_URL, source_url

SCHEMA_VERSION = 2


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
        self.write_line(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        )

    def write_line(self, data: bytes) -> None:
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


class SortedJsonl:
    """Collect rows in read order, then write the final file sorted by (page_id, ordinal).

    The rows are spooled to a temporary file and indexed in memory by position only, so the dump's
    page order never reaches the output and memory stays small. `finish` may rewrite each row, for
    values that need the whole data set (the listing keys).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._spool_path = path.with_suffix(path.suffix + ".spool")
        self._index: list[tuple[int, int, int, int]] = []  # page_id, ordinal, offset, length
        self._handle: BinaryIO | None = None
        self._offset = 0
        self.count = 0
        self.sha256 = ""

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._spool_path.open("wb")
        return self

    def add(self, row: dict[str, Any]) -> None:
        if self._handle is None:
            raise RuntimeError("SortedJsonl must be used in a with block")
        data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._index.append((row["page_id"], row["ordinal"], self._offset, len(data)))
        self._handle.write(data)
        self._offset += len(data)
        self.count += 1

    def finish(self, rewrite: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        if self._handle is None:
            raise RuntimeError("SortedJsonl must be used in a with block")
        self._handle.close()
        self._index.sort()
        with self._spool_path.open("rb") as spool, JsonlWriter(self.path) as out:
            for _, _, offset, length in self._index:
                spool.seek(offset)
                line = spool.read(length)
                out.write_line(line if rewrite is None else _rewritten(line, rewrite))
        self.sha256 = out.sha256
        self._spool_path.unlink()

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
        self._spool_path.unlink(missing_ok=True)


def _rewritten(line: bytes, rewrite: Callable[[dict[str, Any]], dict[str, Any]]) -> bytes:
    row = rewrite(json.loads(line))
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
