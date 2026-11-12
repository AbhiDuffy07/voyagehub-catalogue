import json

import respx

from catalogue.cli import main
from catalogue.discover.dump import sha1_of
from catalogue.discover.slugs import RegistryEntry, save_registry
from tests.dumpfixture import page, write_dump

DATE = "20261101"
SECRET = "Zanzibar Spice Market"


def setup_inputs(tmp_path):
    dump_dir = tmp_path / "dumps" / DATE
    dump_dir.mkdir(parents=True)
    dump = write_dump(
        dump_dir / f"enwikivoyage-{DATE}-pages-articles.xml.bz2",
        [page("Jaipur", f"== See ==\n{{{{see|name={SECRET}|content=Secret prose}}}}", page_id=5)],
    )
    discover = tmp_path / "discover" / DATE
    discover.mkdir(parents=True)
    row = {
        "wikivoyage_page_id": 5,
        "title": "Jaipur",
        "type": "city",
        "status": "guide",
        "role": "destination",
        "ancestors": ["India"],
        "tree_country": "India",
        "tree_country_iso": "IN",
    }
    (discover / "articles.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (discover / "manifest.json").write_text(json.dumps({"dump_sha1": sha1_of(dump)}))
    save_registry(tmp_path / "data" / "slugs.json", {5: RegistryEntry("jaipur", "Jaipur")})
    (tmp_path / "none.txt").write_text("# no proving titles\n", encoding="utf-8")


def fetch(tmp_path, *extra):
    return main(
        [
            "fetch-listings",
            "--dump-date",
            DATE,
            "--dumps-dir",
            str(tmp_path / "dumps"),
            "--discover-dir",
            str(tmp_path / "discover"),
            "--out-dir",
            str(tmp_path / "out"),
            "--data-dir",
            str(tmp_path / "data"),
            "--proving-file",
            str(tmp_path / "none.txt"),
            *extra,
        ],
        environ={},  # no settings: the command is offline
        env_file=None,
    )


def test_it_prints_counts_only_never_listing_text(tmp_path, capsys):
    setup_inputs(tmp_path)

    code = fetch(tmp_path)

    captured = capsys.readouterr()
    assert code == 0
    assert "listings: 1" in captured.out
    assert SECRET not in captured.out + captured.err
    assert "Secret prose" not in captured.out + captured.err
    assert (tmp_path / "out" / DATE / "listings.jsonl").is_file()


def test_it_needs_no_settings_and_makes_no_network_call(tmp_path, capsys):
    setup_inputs(tmp_path)

    with respx.mock(assert_all_called=False) as router:
        code = fetch(tmp_path)

    assert code == 0
    assert len(router.calls) == 0


def test_a_checksum_mismatch_ends_with_one_line_and_exit_code_1(tmp_path, capsys):
    setup_inputs(tmp_path)
    (tmp_path / "discover" / DATE / "manifest.json").write_text(json.dumps({"dump_sha1": "0" * 40}))

    code = fetch(tmp_path)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("error: ")
    assert "does not match" in captured.err
    assert "Traceback" not in captured.err


def test_a_missing_slug_registry_is_refused(tmp_path, capsys):
    setup_inputs(tmp_path)
    (tmp_path / "data" / "slugs.json").unlink()

    code = fetch(tmp_path)

    assert code == 1
    assert "slug registry" in capsys.readouterr().err


def test_the_proving_file_titles_are_reported(tmp_path, capsys):
    setup_inputs(tmp_path)
    proving = tmp_path / "proving.txt"
    proving.write_text("# comment\nJaipur\n", encoding="utf-8")

    code = fetch(tmp_path, "--proving-file", str(proving))

    assert code == 0
    text = (tmp_path / "out" / DATE / "proving-batch.md").read_text(encoding="utf-8")
    assert "| Jaipur | 1 | 0 | 1 |" in text


def test_it_prints_progress_lines_with_counts_only(tmp_path, capsys):
    setup_inputs(tmp_path)

    code = fetch(tmp_path)

    captured = capsys.readouterr()
    assert code == 0 and captured.err == ""
    assert "progress: pages 1 of 1 (100%) done" in captured.out
    assert SECRET not in captured.out
