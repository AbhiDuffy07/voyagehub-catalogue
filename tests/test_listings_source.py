import json

import pytest

from catalogue.errors import PipelineError
from catalogue.listings.source import read_articles


def write_articles(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_articles_are_read_by_page_id_and_by_title(tmp_path):
    rows = [
        {"wikivoyage_page_id": 3, "title": "Kochi", "type": "city"},
        {"wikivoyage_page_id": 9, "title": "Agra", "type": "city"},
    ]
    write_articles(tmp_path / "articles.jsonl", rows)

    by_id, by_title = read_articles(tmp_path / "articles.jsonl")

    assert by_id[9]["title"] == "Agra"
    assert by_title["Kochi"]["wikivoyage_page_id"] == 3


def test_a_missing_articles_file_is_a_clear_error(tmp_path):
    with pytest.raises(PipelineError, match="articles.jsonl"):
        read_articles(tmp_path / "articles.jsonl")


def test_a_damaged_articles_file_is_a_clear_error_that_shows_no_content(tmp_path):
    (tmp_path / "articles.jsonl").write_text("{not json SECRET-TEXT\n", encoding="utf-8")

    with pytest.raises(PipelineError) as caught:
        read_articles(tmp_path / "articles.jsonl")

    assert "SECRET-TEXT" not in str(caught.value)
