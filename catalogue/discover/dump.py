"""Download the Wikivoyage dump and verify it against the SHA-1 Wikimedia publishes.

The dated directory is used, never `latest`: `latest` changes under you, so a checksum read
from it can stop matching the file mid-run. The file is written to `*.part` and renamed only
after the checksum matches, so a half-downloaded or tampered file never has the final name.

The whole file (~133 MB) is held in memory once, because the polite client's retry and
rate-limit logic is built around whole responses. That is fine on a laptop and on runners.
"""

import hashlib
import re
from pathlib import Path

from catalogue.errors import PipelineError
from catalogue.http import PoliteClient

DUMPS_BASE = "https://dumps.wikimedia.org/enwikivoyage"
_DATE = re.compile(r"^\d{8}$")
_CHUNK = 1024 * 1024


def dump_filename(date: str) -> str:
    if not _DATE.match(date):
        raise PipelineError("dump date must be eight digits, like 20261101")
    return f"enwikivoyage-{date}-pages-articles.xml.bz2"


def parse_sha1sums(text: str, filename: str) -> str:
    """The lines look like `<40 hex>  <file name>`."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == filename and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            return parts[0]
    raise PipelineError(f"no checksum published for {filename}")


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 (an integrity check against Wikimedia's list, not security)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_dump(client: PoliteClient, date: str, dumps_dir: Path) -> Path:
    """Return the path of a verified dump, downloading it only if needed."""
    filename = dump_filename(date)
    base = f"{DUMPS_BASE}/{date}"
    expected = parse_sha1sums(client.get(f"{base}/enwikivoyage-{date}-sha1sums.txt").text, filename)

    target_dir = dumps_dir / date
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / filename
    if final.is_file() and sha1_of(final) == expected:
        return final

    part = target_dir / f"{filename}.part"
    try:
        part.write_bytes(client.get(f"{base}/{filename}").content)
        if sha1_of(part) != expected:
            raise PipelineError(f"checksum mismatch for {filename}; the download was discarded")
        part.replace(final)
    finally:
        part.unlink(missing_ok=True)
    return final
