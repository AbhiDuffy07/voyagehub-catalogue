"""Read the `discover` article list that `fetch-listings` works from."""

import json
from pathlib import Path
from typing import Any

from catalogue.errors import PipelineError


def read_articles(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return the article rows by page ID and by title."""
    if not path.is_file():
        raise PipelineError(f"{path.name} not found in {path.parent}; run discover first")
    by_id: dict[int, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                by_id[row["wikivoyage_page_id"]] = row
                by_title[row["title"]] = row
    except (ValueError, KeyError, TypeError):
        raise PipelineError(f"{path.name} is damaged; run discover again") from None
    return by_id, by_title
