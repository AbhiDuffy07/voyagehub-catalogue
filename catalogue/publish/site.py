"""What the built site folder must look like (shared by the build and by deploy)."""

import hashlib
import re

HEADERS = """/v1/index.json
  Cache-Control: public, max-age=300
/v1/manifest.json
  Cache-Control: public, max-age=60
/v1/attributions.json
  Cache-Control: public, max-age=3600
/v1/cities/*
  Cache-Control: public, max-age=3600
"""
# A published file name: lowercase, digits, and - _ . /; no leading dot or slash, no ".." part.
FILE_NAME = re.compile(
    r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9_-]+)*(/[a-z0-9][a-z0-9_-]*(\.[a-z0-9_-]+)*)*$"
)


def version_of(hashes: dict[str, str]) -> str:
    """The site version: a hash over every file name and content hash (manifest excluded)."""
    text = "".join(f"{name}:{digest}\n" for name, digest in sorted(hashes.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
