import json

import respx

from catalogue.cli import main
from tests.readinessfixture import build


def lay_out(tmp_path):
    """Move the fixture's folders to where the command looks (world zones and discover)."""
    kwargs = build(tmp_path)
    dirs = tmp_path / "dirs"
    placed = {
        "listings_in": ("listings_folder", "20261101-IN"),
        "summaries_in": ("summaries_folder", "20261101-IN"),
        "photos_in": ("photos_folder", "20261101-IN"),
        "blocks_in": ("blocks_folder", "20261101-IN"),
        "checks_in": ("checks_folder", "20261101-IN"),
        "timezones_in": ("timezones_folder", "20261101"),
        "discover_in": ("discover_folder", "20261101"),
    }
    for name, (key, folder) in placed.items():
        (dirs / name).mkdir(parents=True)
        kwargs[key].rename(dirs / name / folder)
    return dirs, kwargs["slugs_path"]


def run(dirs, slugs, capsys, *extra):
    argv = [
        "readiness",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--listings-dir",
        str(dirs / "listings_in"),
        "--summaries-dir",
        str(dirs / "summaries_in"),
        "--photos-dir",
        str(dirs / "photos_in"),
        "--blocks-dir",
        str(dirs / "blocks_in"),
        "--checks-dir",
        str(dirs / "checks_in"),
        "--timezones-dir",
        str(dirs / "timezones_in"),
        "--discover-dir",
        str(dirs / "discover_in"),
        "--slugs-path",
        str(slugs),
        "--out-dir",
        str(dirs / "readiness"),
        *extra,
    ]
    return main(argv, environ={}, env_file=None), capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(tmp_path, capsys):
    dirs, slugs = lay_out(tmp_path)
    with respx.mock:  # any network call would fail the test
        code, out = run(dirs, slugs, capsys)
    assert code == 0
    assert "cities assessed: 4" in out.out and "plannable 1, browse only 1" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "readiness" / "20261101-IN" / "city_readiness.jsonl").is_file()


def test_the_previous_folder_carries_the_publication_state(tmp_path, capsys):
    dirs, slugs = lay_out(tmp_path)
    assert run(dirs, slugs, capsys)[0] == 0
    first = dirs / "readiness" / "20261101-IN"
    kept = dirs / "previous"
    first.rename(kept)
    code, out = run(dirs, slugs, capsys, "--previous", str(kept))
    assert code == 0 and "keep 2" in out.out
    state = json.loads((dirs / "readiness" / "20261101-IN" / "state.json").read_text("utf-8"))
    assert state["cities"]["1"]["published"] is True


def test_missing_input_is_one_clear_error(tmp_path, capsys):
    dirs, slugs = lay_out(tmp_path)
    (dirs / "checks_in" / "20261101-IN" / "manifest.json").unlink()
    code, out = run(dirs, slugs, capsys)
    assert code == 1 and "run auto-check first" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    code = main(["readiness", "--dump-date", "2026-11-01"], environ={}, env_file=None)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
