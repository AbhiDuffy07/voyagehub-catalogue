import shutil

import pytest
import respx

from catalogue.cli import main
from tests.photoselectfixture import candidate_row, dest, write_b5


@pytest.fixture
def dirs(tmp_path):
    photos, enrich = write_b5(
        tmp_path / "built",
        destinations=[dest(1, "Jaipur"), dest(2, "Agra")],
        listings=[],
        candidates=[
            candidate_row(1, "Fort.jpg"),
            candidate_row(2, "Small.jpg", width=500, height=400),
        ],
        coverage={"all": {}, "by_country": {"IN": {"articles": {"total": 2, "with_usable": 2}}}},
    )
    (tmp_path / "enrich").mkdir()
    (tmp_path / "photos").mkdir()
    shutil.move(str(enrich), str(tmp_path / "enrich" / "20261101-IN"))
    shutil.move(str(photos), str(tmp_path / "photos" / "20261101-IN"))
    return tmp_path


def select(dirs, capsys, *extra):
    argv = [
        "photo-select",
        "--dump-date",
        "20261101",
        "--country",
        "IN",
        "--photos-dir",
        str(dirs / "photos"),
        "--enrich-dir",
        str(dirs / "enrich"),
        "--out-dir",
        str(dirs / "selection"),
        *extra,
    ]
    code = main(argv, environ={}, env_file=None)
    return code, capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(dirs, capsys):
    with respx.mock:  # any network call would fail the test
        code, out = select(dirs, capsys)
    assert code == 0
    assert "selected: 1 of 2" in out.out and "size gate: removes 1 of 2" in out.out
    assert "OVER THE 5% LINE" in out.out
    assert "files:" in out.out and "20261101-IN" in out.out
    assert (dirs / "selection" / "20261101-IN" / "photo_selection.jsonl").is_file()
    assert "Fort" not in out.out + out.err


def test_missing_photo_output_is_one_clear_error(dirs, capsys):
    shutil.rmtree(dirs / "photos" / "20261101-IN")
    code, out = select(dirs, capsys)
    assert code == 1 and "run photo-candidates first" in out.err and "Traceback" not in out.err


def test_a_bad_dump_date_is_one_clear_error(capsys):
    code = main(["photo-select", "--dump-date", "2026-11-01"], environ={}, env_file=None)
    assert code == 1 and "YYYYMMDD" in capsys.readouterr().err
