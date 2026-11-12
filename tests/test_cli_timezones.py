import shutil

from catalogue.cli import main
from tests.enrichfixture import destination_row, write_enrich_output


def built(tmp_path):
    base = destination_row(1, "Jaipur")
    row = {
        **base,
        "timezone": None,
        "timezone_basis": None,
        "coord_lat": 26.92,
        "coord_long": 75.82,
        "coord_source": "wikivoyage",
    }
    folder = write_enrich_output(
        tmp_path / "built", destinations=[row], listings=[], country_filter="IN"
    )
    target = tmp_path / "enrich"
    target.mkdir()
    shutil.move(str(folder), str(target / "20261101-IN"))
    return target


def derive(enrich_dir, *extra):
    return main(
        [
            "derive-timezone",
            "--dump-date",
            "20261101",
            "--country",
            "IN",
            "--enrich-dir",
            str(enrich_dir),
            "--out-dir",
            str(enrich_dir.parent / "timezones"),
            *extra,
        ],
        environ={},  # offline: no settings needed
        env_file=None,
    )


def test_it_runs_offline_prints_counts_and_writes_the_files(tmp_path, capsys):
    enrich_dir = built(tmp_path)
    code = derive(enrich_dir)
    captured = capsys.readouterr()
    assert code == 0 and captured.err == ""
    assert "from coordinates: 1" in captured.out and "files:" in captured.out
    assert "progress: destinations 1 of 1 (100%) done" in captured.out
    assert (tmp_path / "timezones" / "20261101-IN" / "destination_timezones.jsonl").is_file()


def test_missing_input_is_one_clear_error(tmp_path, capsys):
    enrich_dir = built(tmp_path)
    shutil.rmtree(enrich_dir / "20261101-IN")
    code = derive(enrich_dir)
    captured = capsys.readouterr()
    assert code == 1 and "manifest" in captured.err and "Traceback" not in captured.err
