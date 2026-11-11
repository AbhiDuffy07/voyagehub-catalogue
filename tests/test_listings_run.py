import csv
import hashlib
import json
from datetime import date

import pytest

from catalogue.discover.dump import sha1_of
from catalogue.discover.slugs import RegistryEntry, save_registry
from catalogue.errors import PipelineError
from catalogue.listings.run import run_fetch_listings
from tests.dumpfixture import page, write_dump

TODAY = date(2026, 11, 11)
PROVENANCE = (
    "schema_version",
    "source_url",
    "revision_id",
    "revision_timestamp",
    "licence",
    "licence_url",
    "attribution",
    "dump_date",
    "read_at",
)


def article(page_id, title, kind, status, ancestors, country, iso, role):
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "type": kind,
        "status": status,
        "role": role,
        "ancestors": ancestors,
        "tree_country": country,
        "tree_country_iso": iso,
    }


ARTICLES = [
    article(1, "India", "country", "guide", [], "India", "IN", "browse"),
    article(2, "Kerala", "region", "usable", ["India"], "India", "IN", "browse"),
    article(3, "Kochi", "city", "guide", ["Kerala", "India"], "India", "IN", "destination"),
    article(
        4,
        "Fort Kochi",
        "district",
        "outline",
        ["Kochi", "Kerala", "India"],
        "India",
        "IN",
        "destination_outline",
    ),
    article(6, "Orphan", "district", "usable", ["Gone", "India"], "India", "IN", "destination"),
    article(10, "Nepal", "country", "guide", [], "Nepal", "NP", "browse"),
    article(11, "Pokhara", "city", "usable", ["Nepal"], "Nepal", "NP", "destination"),
    article(12, "Tea tour", "itinerary", "usable", [], None, None, "later_wave"),
]

PAGES = [
    page("India", "Big country.\n== Understand ==\nCurry.", page_id=1, revision=101),
    page("Kerala", "== Cities ==\n{{marker|type=city|name=Kochi|lat=9.9|long=76.2}}", page_id=2),
    page(
        "Kochi",
        "Port city.\n== See ==\n{{see|name=Dutch Palace|lat=9.9|long=76.2|price=Rs 5}}\n"
        "{{see|name=Dutch Palace|lat=9.9|long=76.2}}\n== Eat ==\n{{eat|name=Cafe}}",
        page_id=3,
        revision=303,
    ),
    page("Fort Kochi", "== See ==\n{{see|name=Beach|lastedit=2021-03-04}}", page_id=4),
    page("Orphan", "== See ==\n{{see|name=Lost}}", page_id=6),
    page("Nepal", "Mountains.", page_id=10),
    page("Pokhara", "== Do ==\n{{do|name=Paddle}}", page_id=11),
    page("Tea tour", "== Day 1 ==\n{{see|name=Plantation}}", page_id=12),
    page("Talk:Kochi", "{{see|name=Never}}", page_id=99, ns=1),
    page("Not in list", "{{see|name=Unknown page}}", page_id=77),
]


@pytest.fixture
def world(tmp_path):
    dump = write_dump(tmp_path / "dump.xml.bz2", PAGES)
    discover = tmp_path / "discover" / "20261101"
    discover.mkdir(parents=True)
    rows = sorted(ARTICLES, key=lambda r: r["wikivoyage_page_id"])
    (discover / "articles.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (discover / "manifest.json").write_text(json.dumps({"dump_sha1": sha1_of(dump)}))
    registry = tmp_path / "data" / "slugs.json"
    save_registry(
        registry, {3: RegistryEntry("kochi", "Kochi"), 11: RegistryEntry("pokhara", "Pokhara")}
    )
    return {"dump": dump, "discover": discover, "registry": registry, "tmp": tmp_path}


def run(world, **kwargs):
    return run_fetch_listings(
        dump_path=world["dump"],
        dump_date="20261101",
        discover_folder=world["discover"],
        out_dir=world["tmp"] / "out",
        registry_path=world["registry"],
        today=TODAY,
        **kwargs,
    )


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def listings(result):
    return jsonl(result.output_dir / "listings.jsonl")


def test_all_output_files_are_written(world):
    result = run(world)

    names = {p.name for p in result.output_dir.iterdir()}
    assert {
        "listings.jsonl",
        "sections.jsonl",
        "markers.jsonl",
        "manifest.json",
        "counts_by_country.csv",
        "counts_by_type.csv",
        "report.md",
        "proving-batch.md",
    } <= names
    assert not list(result.output_dir.glob("*.tmp"))


def test_a_listing_row_keeps_the_raw_fields_and_says_where_it_came_from(world):
    result = run(world)

    row = next(r for r in listings(result) if r["name"] == "Dutch Palace" and r["price"])
    assert (row["kind"], row["price"], row["lat"], row["long"], row["wikidata"]) == (
        "see",
        "Rs 5",
        "9.9",
        "76.2",
        "",
    )
    assert (row["page_id"], row["page_title"], row["page_type"], row["role"]) == (
        3,
        "Kochi",
        "city",
        "destination",
    )
    assert (row["section_key"], row["ordinal"]) == ("see", 1)
    assert (row["country"], row["country_iso"]) == ("India", "IN")
    assert row["source_url"].endswith("title=Kochi&oldid=303")
    assert row["extra"] == {}


def test_every_row_in_every_file_carries_provenance(world):
    result = run(world)

    for name in ("listings.jsonl", "sections.jsonl", "markers.jsonl"):
        rows = jsonl(result.output_dir / name)
        assert rows, name
        for row in rows:
            assert all(row.get(key) not in (None, "") for key in PROVENANCE), (name, row)
    row = listings(result)[0]
    assert (row["licence"], row["attribution"], row["dump_date"], row["read_at"]) == (
        "CC BY-SA 4.0",
        "Wikivoyage contributors",
        "2026-11-01",
        "2026-11-11",
    )


def test_district_listings_roll_up_to_the_city_and_the_city_total_counts_both(world):
    result = run(world)

    beach = next(r for r in listings(result) if r["name"] == "Beach")
    assert (beach["owner_page_id"], beach["owner_title"], beach["owner_slug"]) == (
        3,
        "Kochi",
        "kochi",
    )
    assert (beach["via_district"], beach["owner_status"], beach["page_title"]) == (
        True,
        "guide",
        "Fort Kochi",
    )
    assert result.owner_counts[3] == {"own": 3, "from_districts": 1}


def test_a_listing_with_a_broken_parent_chain_has_no_owner_and_is_counted(world):
    result = run(world)

    lost = next(r for r in listings(result) if r["name"] == "Lost")
    assert (lost["owner_page_id"], lost["owner_title"], lost["owner_slug"]) == (None, "", "")
    assert result.totals["owner_unresolved"] == 1


def test_a_repeated_listing_name_on_one_owner_gets_a_numbered_key(world):
    result = run(world)

    keys = [r["listing_key"] for r in listings(result) if r["name"] == "Dutch Palace"]
    assert keys == ["see:dutch-palace", "see:dutch-palace-2"]


def test_listings_on_every_article_are_collected_including_itineraries(world):
    result = run(world)

    names = {r["name"] for r in listings(result)}
    assert {"Plantation", "Paddle", "Cafe"} <= names
    plantation = next(r for r in listings(result) if r["name"] == "Plantation")
    assert (plantation["role"], plantation["country"], plantation["country_iso"]) == (
        "later_wave",
        None,
        None,
    )


def test_only_namespace_zero_pages_in_the_article_list_are_read(world):
    result = run(world)

    names = {r["name"] for r in listings(result)}
    assert "Never" not in names
    assert "Unknown page" not in names
    assert result.totals["pages_not_in_articles"] == 1


def test_sections_carry_text_kind_and_the_page_fields(world):
    result = run(world)

    rows = jsonl(result.output_dir / "sections.jsonl")
    lead = next(r for r in rows if r["page_title"] == "India" and r["section_key"] == "lead")
    assert (lead["text"], lead["kind"], lead["listing_count"]) == ("Big country.", "lead", 0)
    see = next(r for r in rows if r["page_title"] == "Kochi" and r["section_key"] == "see")
    assert (see["text"], see["listing_count"], see["page_type"]) == ("", 2, "city")


def test_markers_go_to_their_own_file_and_not_into_the_listing_counts(world):
    result = run(world)

    (marker,) = jsonl(result.output_dir / "markers.jsonl")
    assert (marker["name"], marker["lat"], marker["page_title"]) == ("Kochi", "9.9", "Kerala")
    assert result.totals["markers"] == 1
    assert all(r["name"] != "Kochi" for r in listings(result))


def test_the_country_filter_keeps_one_country_in_its_own_folder(world):
    result = run(world, country_iso="np")

    assert result.output_dir.name == "20261101-NP"
    assert {r["name"] for r in listings(result)} == {"Paddle"}
    titles = {r["page_title"] for r in jsonl(result.output_dir / "sections.jsonl")}
    assert titles == {"Nepal", "Pokhara"}


def test_a_district_still_finds_its_city_when_the_country_filter_is_on(world):
    result = run(world, country_iso="IN")

    beach = next(r for r in listings(result) if r["name"] == "Beach")
    assert beach["owner_title"] == "Kochi"


def test_two_runs_write_identical_bytes(world):
    first = run(world)
    before = {p.name: p.read_bytes() for p in first.output_dir.iterdir()}
    second = run(world)

    assert {p.name: p.read_bytes() for p in second.output_dir.iterdir()} == before


def test_the_manifest_records_inputs_counts_and_a_hash_of_each_output(world):
    result = run(world)

    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dump_sha1"] == sha1_of(world["dump"])
    assert (
        manifest["articles_sha256"]
        == hashlib.sha256((world["discover"] / "articles.jsonl").read_bytes()).hexdigest()
    )
    assert manifest["totals"]["listings"] == len(listings(result))
    for name, digest in manifest["outputs_sha256"].items():
        assert hashlib.sha256((result.output_dir / name).read_bytes()).hexdigest() == digest
    assert set(manifest["outputs_sha256"]) == {"listings.jsonl", "sections.jsonl", "markers.jsonl"}


def test_counts_by_country_show_own_page_from_districts_and_total(world):
    result = run(world)

    with (result.output_dir / "counts_by_country.csv").open(encoding="utf-8", newline="") as f:
        rows = {r["country_iso"]: r for r in csv.DictReader(f)}
    assert (rows["IN"]["see"], rows["IN"]["eat"], rows["IN"]["total"]) == ("4", "1", "5")
    assert rows["NP"]["do"] == "1"


def test_the_proving_batch_report_lists_own_from_districts_and_total_per_city(world):
    result = run(world, proving_titles=["Kochi", "Pokhara", "Nowhere"])

    text = (result.output_dir / "proving-batch.md").read_text(encoding="utf-8")
    assert "| Kochi | 3 | 1 | 4 |" in text
    assert "| Pokhara | 1 | 0 | 1 |" in text
    assert "| Nowhere | not in the dump" in text


def test_a_detail_report_is_written_for_a_country(world):
    result = run(world, detail_countries=["in"])

    text = (result.output_dir / "detail-IN.md").read_text(encoding="utf-8")
    assert "India (IN)" in text
    assert "2021" in text  # the lastedit year histogram


def test_a_dump_that_does_not_match_the_discover_manifest_is_refused(world):
    manifest = world["discover"] / "manifest.json"
    manifest.write_text(json.dumps({"dump_sha1": "0" * 40}))

    with pytest.raises(PipelineError, match="does not match"):
        run(world)
    assert not (world["tmp"] / "out").exists()


def test_a_missing_discover_manifest_is_refused(world):
    (world["discover"] / "manifest.json").unlink()

    with pytest.raises(PipelineError, match="manifest.json"):
        run(world)


def test_a_missing_dump_is_refused(world):
    world["dump"].unlink()

    with pytest.raises(PipelineError, match="dump"):
        run(world)
