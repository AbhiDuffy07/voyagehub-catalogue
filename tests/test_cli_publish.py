import respx

from catalogue.cli import main
from tests import publishfixture

FOLDER = "20261101-IN"


def lay_out(tmp_path):
    """Move the fixture's folders to where the command looks."""
    kwargs = publishfixture.build(tmp_path)
    dirs = tmp_path / "dirs"
    placed = {
        "readiness": ("readiness_folder", FOLDER),
        "listings": ("listings_folder", FOLDER),
        "summaries": ("summaries_folder", FOLDER),
        "photos": ("photos_folder", FOLDER),
        "blocks": ("blocks_folder", FOLDER),
        "checks": ("checks_folder", FOLDER),
        "timezones": ("timezones_folder", "20261101"),
        "prices": ("prices_folder", FOLDER),
    }
    for name, (key, folder) in placed.items():
        (dirs / name).mkdir(parents=True)
        kwargs[key].rename(dirs / name / folder)
    return dirs


def run(dirs, capsys, *extra):
    argv = ["publish-static", "--dump-date", "20261101", "--country", "IN"]
    for flag, name in (
        ("--readiness-dir", "readiness"),
        ("--listings-dir", "listings"),
        ("--summaries-dir", "summaries"),
        ("--photos-dir", "photos"),
        ("--blocks-dir", "blocks"),
        ("--checks-dir", "checks"),
        ("--timezones-dir", "timezones"),
        ("--prices-dir", "prices"),
    ):
        argv += [flag, str(dirs / name)]
    argv += ["--out-dir", str(dirs / "publish"), *extra]
    return main(argv, environ={}, env_file=None), capsys.readouterr()


def test_runs_offline_without_settings_and_prints_counts(tmp_path, capsys):
    dirs = lay_out(tmp_path)
    with respx.mock:  # any network call would fail the test
        code, out = run(dirs, capsys)
    assert code == 0
    assert "cities published: 2" in out.out
    assert "site:" in out.out and FOLDER in out.out
    assert (dirs / "publish" / FOLDER / "site" / "v1" / "manifest.json").is_file()


def test_an_input_problem_is_one_error_line_and_exit_1(tmp_path, capsys):
    dirs = lay_out(tmp_path)
    (dirs / "prices" / FOLDER / "block_costs.jsonl").write_text("{}\n", encoding="utf-8")
    code, out = run(dirs, capsys)
    assert code == 1
    assert "error:" in out.err and "block_costs.jsonl" in out.err
