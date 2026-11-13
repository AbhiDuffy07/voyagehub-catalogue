import csv
import hashlib
import json
from datetime import date

import pytest
import respx

from catalogue.errors import PipelineError
from catalogue.photos.run import run_photo_candidates
from tests.apifakes import fake_wikidata, string_claim
from tests.enrichfixture import destination_row, listing_fact, write_enrich_output
from tests.photofakes import COMMONS, commons_file, fake_commons

WD = "https://www.wikidata.org/w/api.php"
TODAY = date(2026, 11, 13)

NC = {"licence": "cc-by-nc-4.0", "licence_name": "CC BY-NC 4.0"}
MARKED = ["Quality images"]


def dest(page_id, title, qid, image=None, iso="IN"):
    return {**destination_row(page_id, title, qid=qid, iso=iso), "image": image}


def listing(page_id, ordinal, key, qid, image=None, kind="see", iso="IN"):
    return {**listing_fact(page_id, ordinal, key, kind=kind, qid=qid, iso=iso), "image": image}


DESTINATIONS = [
    dest(1, "Jaipur", "Q1", image="Jaipur.jpg"),
    dest(2, "Agra", "Q2"),  # several P18
    dest(3, "Delhi", "Q3"),  # no P18, a Commons category with marked files
    dest(4, "Nowhere", None),  # no item
    dest(5, "Pune", "Q5"),  # one P18 that enrich-wikidata did not see
    dest(6, "Paris", "Q6", image="Paris.jpg", iso="FR"),
]
LISTINGS = [
    listing(1, 1, "see:amber", "Q10", image="Amer.jpg"),  # non-commercial licence
    listing(1, 2, "do:walk", "Q11"),  # nothing at all
    listing(1, 3, "eat:cafe", "Q12", image="Cafe.jpg", kind="eat"),  # not see/do: out
    listing(1, 4, "see:gone", "Q13", image="Gone.jpg"),  # file missing on Commons
]
CLAIMS = {
    "Q1": {"P18": [string_claim("Jaipur.jpg")]},
    "Q2": {"P18": [string_claim("Agra A.jpg"), string_claim("Agra B.jpg")]},
    "Q3": {"P373": [string_claim("Delhi")]},
    "Q5": {"P18": [string_claim("Pune.jpg")]},
    "Q6": {"P18": [string_claim("Paris.jpg")]},
    "Q10": {"P18": [string_claim("Amer.jpg")]},
    "Q11": {},
    "Q13": {"P18": [string_claim("Gone.jpg")]},
}
FILES = {
    "File:Jaipur.jpg": commons_file(marks=MARKED),
    "File:Agra A.jpg": commons_file(),
    "File:Agra B.jpg": commons_file(artist=None),
    "File:Delhi 1.jpg": commons_file(marks=MARKED),
    "File:Delhi 2.jpg": commons_file(marks=MARKED, mime="image/svg+xml"),
    "File:Pune.jpg": commons_file(),
    "File:Paris.jpg": commons_file(),
    "File:Amer.jpg": commons_file(**NC),
}
CATEGORIES = {"Delhi": ["File:Delhi 2.jpg", "File:Delhi 1.jpg"]}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def folders(tmp_path):
    enrich = write_enrich_output(
        tmp_path / "enrich" / "20261101",
        destinations=DESTINATIONS,
        listings=LISTINGS,
        country_filter=None,
    )
    listings = tmp_path / "listings" / "20261101"
    listings.mkdir(parents=True)
    rows = [
        {"kind": "see", "country_iso": "IN"},
        {"kind": "see", "country_iso": "IN"},
        {"kind": "do", "country_iso": "IN"},
        {"kind": "see", "country_iso": "IN"},
        {"kind": "see", "country_iso": "IN"},  # a see listing with no facts row: coverage counts it
        {"kind": "eat", "country_iso": "IN"},
        {"kind": "see", "country_iso": "FR"},
    ]
    (listings / "listings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (listings / "manifest.json").write_text(
        json.dumps({"outputs_sha256": {"listings.jsonl": _sha(listings / "listings.jsonl")}}),
        encoding="utf-8",
    )
    manifest = json.loads((enrich / "manifest.json").read_text(encoding="utf-8"))
    manifest["listings_manifest_sha256"] = _sha(listings / "manifest.json")
    (enrich / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return enrich, listings


def run(client, folders, tmp_path, **kw):
    enrich, listings = folders
    return run_photo_candidates(
        client,
        enrich_folder=enrich,
        listings_folder=listings,
        out_dir=tmp_path / "out",
        dump_date="20261101",
        today=TODAY,
        **kw,
    )


def mock(files=None, categories=None, claims=None, seen=None, wd_seen=None, search_seen=None):
    respx.get(WD).mock(side_effect=fake_wikidata(claims or CLAIMS, seen=wd_seen))
    respx.get(COMMONS).mock(
        side_effect=fake_commons(
            FILES if files is None else files,
            CATEGORIES if categories is None else categories,
            seen=seen,
            search_seen=search_seen,
        )
    )


def rows_of(result):
    path = result.output_dir / "photo_candidates.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def pick(rows, page_id, ordinal=0):
    return {
        r["commons_file"]: r for r in rows if (r["page_id"], r["ordinal"]) == (page_id, ordinal)
    }


@respx.mock
def test_candidates_per_source_with_flags(client, folders, tmp_path):
    mock()
    rows = rows_of(run(client, folders, tmp_path))

    jaipur = pick(rows, 1)["Jaipur.jpg"]
    assert jaipur["sources"] == ["wikidata_p18"] and jaipur["flags"] == []
    assert jaipur["usable"] is True
    assert jaipur["author"] == "Ann & Co"
    assert jaipur["attribution"] == "Ann & Co, CC BY-SA 4.0, via Wikimedia Commons"
    assert jaipur["quality_marks"] == ["Quality images"]
    assert jaipur["thumb_width"] == 1280 and jaipur["description_url"].endswith("File:Jaipur.jpg")

    agra = pick(rows, 2)
    assert set(agra) == {"Agra A.jpg", "Agra B.jpg"}  # none is chosen
    assert all("multiple_p18" in r["flags"] for r in agra.values())
    assert agra["Agra B.jpg"]["flags"] == ["multiple_p18", "no_author"]
    assert agra["Agra B.jpg"]["usable"] is False and agra["Agra B.jpg"]["attribution"] is None

    delhi = pick(rows, 3)
    assert delhi["Delhi 1.jpg"]["sources"] == ["commons_category"]
    assert delhi["Delhi 1.jpg"]["usable"] is True
    assert delhi["Delhi 2.jpg"]["flags"] == ["not_an_image"]
    assert delhi["Delhi 2.jpg"]["usable"] is False

    assert pick(rows, 4) == {}
    assert pick(rows, 5)["Pune.jpg"]["flags"] == ["p18_after_facts"]

    assert pick(rows, 1, 1)["Amer.jpg"]["flags"] == ["licence_not_allowed"]
    assert pick(rows, 1, 1)["Amer.jpg"]["usable"] is False
    assert pick(rows, 1, 2) == {}
    assert pick(rows, 1, 3) == {}  # an eat listing is not a subject
    gone = pick(rows, 1, 4)["Gone.jpg"]
    assert gone["flags"] == ["file_missing"] and gone["usable"] is False
    assert gone["url"] is None and gone["author"] is None


@respx.mock
def test_missing_licence_is_flagged_unknown_not_guessed(client, folders, tmp_path):
    files = {**FILES, "File:Pune.jpg": commons_file(licence=None, licence_name=None)}
    mock(files=files)
    rows = rows_of(run(client, folders, tmp_path))
    pune = pick(rows, 5)["Pune.jpg"]
    assert "licence_unknown" in pune["flags"] and pune["usable"] is False
    assert pune["licence_code"] is None and pune["attribution"] is None


@respx.mock
def test_no_thumbnail_makes_a_candidate_unusable(client, folders, tmp_path):
    mock(files={**FILES, "File:Pune.jpg": commons_file(thumb=False)})
    pune = pick(rows_of(run(client, folders, tmp_path)), 5)["Pune.jpg"]
    assert "no_thumb_url" in pune["flags"] and pune["usable"] is False


@respx.mock
def test_category_cap_keeps_ten_marked_files_and_flags_the_cap(client, folders, tmp_path):
    names = [f"File:D{n:02d}.jpg" for n in range(14)]
    files = {n: commons_file(marks=MARKED) for n in names}
    mock(files={**FILES, **files}, categories={"Delhi": names})
    rows = pick(rows_of(run(client, folders, tmp_path)), 3)
    assert sorted(rows) == [n.removeprefix("File:") for n in names[:10]]
    assert all("category_cap_reached" in r["flags"] for r in rows.values())


@respx.mock
def test_a_file_in_both_sources_is_one_row_with_both(client, folders, tmp_path):
    claims = {
        **CLAIMS,
        "Q3": {"P18": [string_claim("Delhi 1.jpg")], "P373": [string_claim("Delhi")]},
    }
    mock(claims=claims)
    delhi = pick(rows_of(run(client, folders, tmp_path)), 3)
    assert delhi["Delhi 1.jpg"]["sources"] == ["commons_category", "wikidata_p18"]
    assert delhi["Delhi 1.jpg"]["flags"] == ["p18_after_facts"]


@respx.mock
def test_each_item_category_and_file_is_asked_once_in_batches(client, folders, tmp_path):
    seen, wd_seen, search_seen = [], [], []
    mock(seen=seen, wd_seen=wd_seen, search_seen=search_seen)
    run(client, folders, tmp_path)
    asked = [q for batch in wd_seen for q in batch]
    assert len(asked) == len(set(asked)) == 8  # each subject with an item, once
    assert len(search_seen) == 1
    files = [t for batch in seen for t in batch]
    assert len(files) == len(set(files))


@respx.mock
def test_coverage_counts_listings_without_an_item_against_it(client, folders, tmp_path):
    mock()
    result = run(client, folders, tmp_path)
    totals = result.totals["coverage"]
    # Indian destinations: Nowhere has no item; the other four each have a usable candidate
    # (Agra's "A" file is fine although "B" has no author).
    assert totals["by_country"]["IN"]["articles"] == {"total": 5, "with_usable": 4}
    assert totals["by_country"]["FR"]["articles"] == {"total": 1, "with_usable": 1}
    # fetch-listings has 5 see/do listings in India (one has no facts row); none has a usable photo.
    assert totals["by_country"]["IN"]["listings"] == {"total": 5, "with_usable": 0}
    assert totals["all"]["articles"] == {"total": 6, "with_usable": 5}
    table = list(csv.DictReader((result.output_dir / "coverage_by_country.csv").open()))
    row = next(r for r in table if r["country_iso"] == "IN" and r["subject"] == "articles")
    assert (row["total"], row["with_usable_candidate"], row["percent"]) == ("5", "4", "80.0")
    assert "Destinations: 5 of 6 (83.3%)" in (result.output_dir / "report.md").read_text("utf-8")


@respx.mock
def test_outputs_and_manifest_hashes(client, folders, tmp_path):
    mock()
    result = run(client, folders, tmp_path)
    folder = result.output_dir
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["photo_rules_version"] >= 1
    assert manifest["country_filter"] is None
    assert set(manifest["outputs_sha256"]) == {
        "coverage_by_country.csv",
        "flags_by_country.csv",
        "licences.csv",
        "photo_candidates.jsonl",
        "photo_candidates_by_country.csv",
        "report.md",
    }
    for name, digest in manifest["outputs_sha256"].items():
        assert _sha(folder / name) == digest
    licences = list(csv.DictReader((folder / "licences.csv").open(encoding="utf-8")))
    nc = next(r for r in licences if r["licence_code"] == "cc-by-nc-4.0")
    assert nc["allowed"] == "no"
    assert "cc-by-sa-4.0" in {r["licence_code"] for r in licences}


@respx.mock
def test_second_run_is_identical(client, folders, tmp_path):
    mock()
    first = run(client, folders, tmp_path)
    before = {p.name: p.read_bytes() for p in first.output_dir.iterdir()}
    second = run(client, folders, tmp_path)
    assert {p.name: p.read_bytes() for p in second.output_dir.iterdir()} == before


@respx.mock
def test_summary_is_counts_only(client, folders, tmp_path):
    mock()
    summary = run(client, folders, tmp_path).summary()
    assert "coverage articles: 5 of 6 (83.3%)" in summary
    assert "Jaipur" not in summary and "example.invalid" not in summary


@respx.mock
def test_an_edited_facts_file_is_refused(client, folders, tmp_path):
    mock()
    enrich, _ = folders
    with (enrich / "destination_facts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(PipelineError, match="does not match the enrich-wikidata manifest"):
        run(client, folders, tmp_path)


@respx.mock
def test_a_different_country_filter_is_refused(client, folders, tmp_path):
    mock()
    with pytest.raises(PipelineError, match="different country filter"):
        run(client, folders, tmp_path, country_iso="IN")


@respx.mock
def test_listings_that_are_not_the_ones_enrich_read_are_refused(client, folders, tmp_path):
    mock()
    _, listings = folders
    with (listings / "listings.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "see", "country_iso": "IN"}) + "\n")
    with pytest.raises(PipelineError, match="does not match the fetch-listings manifest"):
        run(client, folders, tmp_path)


@respx.mock
def test_a_different_listings_manifest_is_refused(client, folders, tmp_path):
    mock()
    _, listings = folders
    manifest = json.loads((listings / "manifest.json").read_text(encoding="utf-8"))
    manifest["extra"] = 1
    (listings / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="not the one enrich-wikidata read"):
        run(client, folders, tmp_path)


def test_missing_listings_folder_names_the_job_to_run(client, folders, tmp_path):
    enrich, listings = folders
    (listings / "manifest.json").unlink()
    with pytest.raises(PipelineError, match="run fetch-listings"):
        run(client, folders, tmp_path)
