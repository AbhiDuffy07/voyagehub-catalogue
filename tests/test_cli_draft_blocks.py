import respx

from catalogue.cli import main
from tests.blocksfixture import fact_row, listing_row, write_inputs


def build(tmp_path):
    rows = [listing_row(i + 1, f"Place {i}", km_north=i * 0.2) for i in range(5)]
    lis, enr, pho = write_inputs(tmp_path / "built", rows, [fact_row(r) for r in rows])
    built = lis.parent
    (tmp_path / "discover").mkdir()
    (built / "discover").rename(tmp_path / "discover" / "20261101")  # the world folder
    (tmp_path / "summaries").mkdir()
    (built / "summaries").rename(tmp_path / "summaries" / "20261101-IN")
    for name, src in (("listings", lis), ("enrich", enr), ("photo_selection", pho)):
        (tmp_path / name).mkdir()
        src.rename(tmp_path / name / "20261101-IN")
    return tmp_path


def draft(dirs, capsys, *extra):
    argv = [
        "draft-blocks",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--listings-dir",
        str(dirs / "listings"),
        "--enrich-dir",
        str(dirs / "enrich"),
        "--photos-dir",
        str(dirs / "photo_selection"),
        "--discover-dir",
        str(dirs / "discover"),
        "--summaries-dir",
        str(dirs / "summaries"),
        "--out-dir",
        str(dirs / "blocks"),
        *extra,
    ]
    return main(argv, environ={}, env_file=None), capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(tmp_path, capsys):
    dirs = build(tmp_path)
    with respx.mock:  # any network call would fail the test
        code, out = draft(dirs, capsys)
    assert code == 0
    assert "blocks: 1 in 1 cities" in out.out and "days they can fill: 0" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "blocks" / "20261101-IN" / "day_blocks.jsonl").is_file()
    assert "Place" not in out.out + out.err


def test_missing_input_is_one_clear_error(tmp_path, capsys):
    dirs = build(tmp_path)
    (dirs / "photo_selection" / "20261101-IN" / "manifest.json").unlink()
    code, out = draft(dirs, capsys)
    assert code == 1 and "run photo-select first" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    code = main(["draft-blocks", "--dump-date", "2026-11-01"], environ={}, env_file=None)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
