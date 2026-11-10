import hashlib

import pytest
import respx

from catalogue.discover.dump import dump_filename, fetch_dump, parse_sha1sums
from catalogue.errors import PipelineError

DATE = "20261101"
BASE = f"https://dumps.wikimedia.org/enwikivoyage/{DATE}"
NAME = f"enwikivoyage-{DATE}-pages-articles.xml.bz2"
PAYLOAD = b"not really bz2, only bytes for the checksum"
GOOD_SHA1 = hashlib.sha1(PAYLOAD).hexdigest()  # noqa: S324 (Wikimedia publishes SHA-1)


def sums(sha1: str = GOOD_SHA1) -> str:
    return f"{'0' * 40}  enwikivoyage-{DATE}-abstract.xml\n{sha1}  {NAME}\n"


def test_the_dump_file_name_follows_wikimedias_pattern():
    assert dump_filename(DATE) == NAME


@pytest.mark.parametrize("bad", ["latest", "2026-11-01", "../x", "2026110", ""])
def test_a_dump_date_must_be_eight_digits(bad):
    with pytest.raises(PipelineError, match="dump date"):
        dump_filename(bad)


def test_parse_sha1sums_finds_the_hash_for_one_file():
    assert parse_sha1sums(sums(), NAME) == GOOD_SHA1


def test_parse_sha1sums_rejects_a_file_that_is_not_listed():
    with pytest.raises(PipelineError, match="no checksum"):
        parse_sha1sums(sums(), "other.xml.bz2")


@respx.mock
def test_download_is_verified_then_moved_into_place(client, tmp_path):
    respx.get(f"{BASE}/enwikivoyage-{DATE}-sha1sums.txt").respond(200, text=sums())
    respx.get(f"{BASE}/{NAME}").respond(200, content=PAYLOAD)

    path = fetch_dump(client, DATE, tmp_path)

    assert path == tmp_path / DATE / NAME
    assert path.read_bytes() == PAYLOAD
    assert not list((tmp_path / DATE).glob("*.part"))


@respx.mock
def test_a_checksum_mismatch_leaves_no_file_behind(client, tmp_path):
    respx.get(f"{BASE}/enwikivoyage-{DATE}-sha1sums.txt").respond(200, text=sums("a" * 40))
    respx.get(f"{BASE}/{NAME}").respond(200, content=PAYLOAD)

    with pytest.raises(PipelineError, match="checksum"):
        fetch_dump(client, DATE, tmp_path)

    assert list((tmp_path / DATE).iterdir()) == []


@respx.mock
def test_a_verified_file_on_disk_is_not_downloaded_again(client, tmp_path):
    (tmp_path / DATE).mkdir()
    (tmp_path / DATE / NAME).write_bytes(PAYLOAD)
    respx.get(f"{BASE}/enwikivoyage-{DATE}-sha1sums.txt").respond(200, text=sums())
    big = respx.get(f"{BASE}/{NAME}").respond(200, content=b"should not be fetched")

    path = fetch_dump(client, DATE, tmp_path)

    assert path.read_bytes() == PAYLOAD
    assert big.call_count == 0


@respx.mock
def test_a_corrupt_file_on_disk_is_replaced(client, tmp_path):
    (tmp_path / DATE).mkdir()
    (tmp_path / DATE / NAME).write_bytes(b"half a download")
    respx.get(f"{BASE}/enwikivoyage-{DATE}-sha1sums.txt").respond(200, text=sums())
    respx.get(f"{BASE}/{NAME}").respond(200, content=PAYLOAD)

    path = fetch_dump(client, DATE, tmp_path)

    assert path.read_bytes() == PAYLOAD
