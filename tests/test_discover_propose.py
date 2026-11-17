import json
import random

import pytest

from catalogue.cli import main
from catalogue.discover.propose import ProposeRequest, propose_slugs, run_propose_slugs
from catalogue.discover.slugs import RegistryEntry, load_registry, save_registry
from catalogue.errors import PipelineError


def req(page_id, title, iso="IN", regions=()):
    return ProposeRequest(page_id=page_id, title=title, country_iso=iso, regions=tuple(regions))


def test_a_free_name_gets_its_plain_folded_slug():
    registry, events = propose_slugs([req(1, "São Paulo", "BR")], {})

    assert registry[1] == RegistryEntry("sao-paulo", "São Paulo")
    assert events == []


def test_a_taken_name_gets_the_region_and_the_existing_slug_is_untouched():
    old = {5: RegistryEntry("aurangabad", "Aurangabad")}

    registry, events = propose_slugs([req(9, "Aurangabad", regions=["Bihar", "India"])], old)

    assert registry[5] == old[5]
    assert registry[9].slug == "aurangabad-bihar"
    assert [(e.kind, e.page_id) for e in events] == [("collision_region", 9)]


def test_two_new_cities_with_one_name_the_lower_id_keeps_the_plain_name():
    registry, events = propose_slugs(
        [req(30, "Salem", regions=["Oregon"]), req(20, "Salem", regions=["Tamil Nadu"])], {}
    )

    assert registry[20].slug == "salem"
    assert registry[30].slug == "salem-oregon"
    assert len(events) == 1


def test_without_a_region_the_country_code_is_used():
    old = {1: RegistryEntry("kochi", "Kochi")}

    registry, events = propose_slugs([req(2, "Kochi", "JP")], old)

    assert registry[2].slug == "kochi-jp"
    assert events[0].kind == "collision_country"


def test_a_region_equal_to_the_name_is_skipped_for_the_next_one():
    old = {1: RegistryEntry("goa", "Goa")}

    registry, _ = propose_slugs([req(2, "Goa", regions=["Goa", "West India"])], old)

    assert registry[2].slug == "goa-west-india"


def test_when_region_and_country_forms_are_taken_the_page_id_is_added():
    old = {
        1: RegistryEntry("x", "X"),
        2: RegistryEntry("x-bihar", "X"),
        3: RegistryEntry("x-in", "X"),
    }

    registry, events = propose_slugs([req(4, "X", regions=["Bihar"])], old)

    assert registry[4].slug == "x-in-4"
    assert events[0].kind == "collision_id"


def test_a_page_without_country_gets_the_page_id():
    old = {1: RegistryEntry("x", "X")}

    registry, _ = propose_slugs([req(8, "X", None)], old)

    assert registry[8].slug == "x-8"


def test_a_title_without_latin_letters_falls_back_to_the_page_id():
    registry, events = propose_slugs([req(77, "東京", "JP")], {})

    assert registry[77].slug == "place-77"
    assert events[0].kind == "fallback"


def test_existing_entries_never_change_even_with_a_new_title_or_a_repeat_request():
    old = {5: RegistryEntry("jaipur", "Jaipur (old title)")}

    registry, events = propose_slugs([req(5, "Jaipur"), req(6, "Agra")], old)

    assert registry[5] == old[5]
    assert registry[6].slug == "agra"
    assert events == []


def test_the_same_input_gives_the_same_output_in_any_order():
    old = {1: RegistryEntry("salem", "Salem"), 2: RegistryEntry("bath", "Bath")}
    requests = [
        req(10, "Salem", regions=["Oregon"]),
        req(11, "Salem", regions=["Massachusetts"]),
        req(12, "Bath", "GB", regions=["Somerset"]),
        req(13, "Bath", "US", regions=["Maine"]),
        req(14, "Zürich", "CH"),
    ]
    first, _ = propose_slugs(requests, old)
    shuffled = requests[:]
    random.Random(3).shuffle(shuffled)  # noqa: S311

    second, _ = propose_slugs(shuffled, old)

    assert first == second
    assert len({e.slug for e in first.values()}) == len(first)


def test_the_input_registry_is_not_modified():
    old = {1: RegistryEntry("a", "A")}

    propose_slugs([req(2, "B")], old)

    assert old == {1: RegistryEntry("a", "A")}


def article(page_id, title, iso="IN", parent=None, ancestors=(), country="India", type_="city"):
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "type": type_,
        "tree_country_iso": iso,
        "tree_country": country,
        "parent": parent,
        "ancestors": list(ancestors),
        "role": "destination_outline",
    }


def lay_out(tmp_path, rows, registry=None):
    folder = tmp_path / "discover" / "20261101"
    folder.mkdir(parents=True)
    (folder / "articles.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    path = tmp_path / "data" / "slugs.json"
    save_registry(path, registry or {})
    return folder, path


def test_the_run_adds_slugs_for_city_articles_only_and_writes_a_report(tmp_path):
    folder, path = lay_out(
        tmp_path,
        [
            article(1, "Aurangabad", parent="Maharashtra", ancestors=["Maharashtra", "India"]),
            article(2, "Aurangabad", parent="Bihar", ancestors=["Bihar", "India"]),
            article(3, "Bihar", type_="region"),
            article(4, "Nowhere", iso=None, country=None),
        ],
    )

    result = run_propose_slugs(articles_path=folder / "articles.jsonl", registry_path=path)

    saved = load_registry(path)
    assert {pid: e.slug for pid, e in saved.items()} == {
        1: "aurangabad",
        2: "aurangabad-bihar",
        4: "nowhere",
    }
    assert result.added == 3
    assert result.added_by_country["IN"] == 2
    assert result.collisions == {"collision_region": 1, "collision_id": 0, "collision_country": 0}
    assert (path.parent / "slugs.json").read_bytes().endswith(b"}\n")


def test_a_second_run_changes_nothing(tmp_path):
    folder, path = lay_out(tmp_path, [article(1, "Jaipur"), article(2, "Jaipur")])
    run_propose_slugs(articles_path=folder / "articles.jsonl", registry_path=path)
    before = path.read_bytes()

    result = run_propose_slugs(articles_path=folder / "articles.jsonl", registry_path=path)

    assert result.added == 0
    assert path.read_bytes() == before


def test_a_missing_registry_or_articles_file_is_an_error(tmp_path):
    folder, path = lay_out(tmp_path, [article(1, "Jaipur")])

    with pytest.raises(PipelineError, match="registry"):
        run_propose_slugs(
            articles_path=folder / "articles.jsonl", registry_path=tmp_path / "no.json"
        )
    with pytest.raises(PipelineError, match="articles"):
        run_propose_slugs(articles_path=tmp_path / "no.jsonl", registry_path=path)


def test_the_cli_command_writes_the_registry_and_prints_counts(tmp_path, capsys):
    folder, path = lay_out(tmp_path, [article(1, "Jaipur"), article(2, "Jaipur", parent="Raj")])

    code = main(
        [
            "propose-slugs",
            "--dump-date",
            "20261101",
            "--discover-dir",
            str(tmp_path / "discover"),
            "--data-dir",
            str(tmp_path / "data"),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "slugs added: 2" in out
    assert load_registry(path)[2].slug == "jaipur-raj"
    assert (folder / "slug_proposals.md").is_file()


def test_the_report_has_twenty_examples_with_five_collisions(tmp_path):
    rows = [article(i, f"Town {i}") for i in range(100, 160)]
    rows += [article(i, "Dupe", parent=f"Reg {i}") for i in range(200, 215)]
    folder, path = lay_out(tmp_path, rows)

    run_propose_slugs(articles_path=folder / "articles.jsonl", registry_path=path)

    text = (folder / "slug_proposals.md").read_text(encoding="utf-8")
    examples = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and "->" in line and line[2].isdigit()
    ]
    assert len(examples) == 20
    assert sum("collision" in line for line in examples) == 5
