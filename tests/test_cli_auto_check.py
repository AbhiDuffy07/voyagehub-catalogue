import respx

from catalogue.cli import main
from tests.checkfixture import make_blocks


def build(tmp_path):
    folder = make_blocks(
        tmp_path, {"jaipur": [("Amer Fort", "fort_palace"), ("Eros Cinema", None)]}
    )
    (tmp_path / "blocks_in").mkdir()
    folder.rename(tmp_path / "blocks_in" / "20261101-IN")
    return tmp_path


def run(dirs, capsys):
    argv = [
        "auto-check",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--blocks-dir",
        str(dirs / "blocks_in"),
        "--out-dir",
        str(dirs / "checks"),
    ]
    return main(argv, environ={}, env_file=None), capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(tmp_path, capsys):
    dirs = build(tmp_path)
    with respx.mock:  # any network call would fail the test
        code, out = run(dirs, capsys)
    assert code == 0
    assert "rejected 1" in out.out and "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "checks" / "20261101-IN" / "block_checks.jsonl").is_file()
    assert "Cinema" not in out.out + out.err


def test_missing_input_is_one_clear_error(tmp_path, capsys):
    dirs = build(tmp_path)
    (dirs / "blocks_in" / "20261101-IN" / "manifest.json").unlink()
    code, out = run(dirs, capsys)
    assert code == 1 and "run draft-blocks first" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    code = main(["auto-check", "--dump-date", "2026-11-01"], environ={}, env_file=None)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
