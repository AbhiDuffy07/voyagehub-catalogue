import respx

from catalogue.blocks.check_run import run_auto_check
from catalogue.cli import main
from tests.checkfixture import make_blocks
from tests.test_prices_run import JAIPUR, TODAY, write_listings


def build(tmp_path):
    blocks = make_blocks(tmp_path, {"jaipur": JAIPUR})
    checks = run_auto_check(
        blocks_folder=blocks,
        out_dir=tmp_path / "checks_out",
        dump_date="20261101",
        today=TODAY,
        country_iso="IN",
    ).output_dir
    listings = write_listings(tmp_path, {})
    for name, folder in (("blocks_in", blocks), ("checks_in", checks), ("listings_in", listings)):
        (tmp_path / name).mkdir()
        folder.rename(tmp_path / name / "20261101-IN")
    return tmp_path


def run(dirs, capsys, *extra):
    argv = [
        "price-bands",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--listings-dir",
        str(dirs / "listings_in"),
        "--blocks-dir",
        str(dirs / "blocks_in"),
        "--checks-dir",
        str(dirs / "checks_in"),
        "--out-dir",
        str(dirs / "prices"),
        *extra,
    ]
    return main(argv, environ={}, env_file=None), capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(tmp_path, capsys):
    dirs = build(tmp_path)
    with respx.mock:  # any network call would fail the test
        code, out = run(dirs, capsys)
    assert code == 0
    assert "city bands: 0" in out.out and "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "prices" / "20261101-IN" / "block_costs.jsonl").is_file()


def test_missing_input_is_one_clear_error(tmp_path, capsys):
    dirs = build(tmp_path)
    (dirs / "checks_in" / "20261101-IN" / "manifest.json").unlink()
    code, out = run(dirs, capsys)
    assert code == 1 and "run auto-check first" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    code = main(["price-bands", "--dump-date", "2026-11-01"], environ={}, env_file=None)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
