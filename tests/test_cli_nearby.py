import json
import shutil

import respx

from catalogue.cli import main
from catalogue.http import PoliteClient
from tests.nearbyfixture import COMMONS, WIKIDATA, build, fake_commons_api, fake_wikidata_api

EMAIL = "pipeline@example.invalid"


def quick_client(settings):
    return PoliteClient(settings, sleep=lambda seconds: None)


def layout(tmp_path):
    """The fixture folders, moved to where the CLI looks for them (`<base>/<date>[-CC]`)."""
    folders = build(tmp_path)
    base = tmp_path / "out"
    for key, name, folder in (
        ("discover_folder", "discover", "20261101"),
        ("listings_folder", "listings", "20261101-IN"),
        ("enrich_folder", "enrich", "20261101-IN"),
        ("photos_folder", "photos", "20261101-IN"),
        ("selection_folder", "selection", "20261101-IN"),
    ):
        target = base / name / folder
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folders[key]), str(target))
    return base


def args(base, *extra):
    return [
        "nearby",
        "--dump-date",
        "20261101",
        "--country",
        "in",
        "--discover-dir",
        str(base / "discover"),
        "--listings-dir",
        str(base / "listings"),
        "--enrich-dir",
        str(base / "enrich"),
        "--photos-dir",
        str(base / "photos"),
        "--selection-dir",
        str(base / "selection"),
        "--cache-dir",
        str(base / "cache"),
        "--out-dir",
        str(base / "nearby"),
        *extra,
    ]


@respx.mock
def test_nearby_then_photo_select_with_the_nearby_folder(tmp_path, capsys):
    base = layout(tmp_path)
    respx.get(WIKIDATA).mock(side_effect=fake_wikidata_api())
    respx.get(COMMONS).mock(side_effect=fake_commons_api())
    code = main(
        args(base), environ={"CONTACT_EMAIL": EMAIL}, env_file=None, client_factory=quick_client
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "short of photographed places: 1" in out and EMAIL not in out
    folder = base / "nearby" / "20261101-IN"
    assert (folder / "nearby_places.jsonl").is_file()
    assert list((base / "cache").rglob("*.json"))

    code = main(
        [
            "photo-select",
            "--dump-date",
            "20261101",
            "--country",
            "IN",
            "--photos-dir",
            str(base / "photos"),
            "--enrich-dir",
            str(base / "enrich"),
            "--nearby-dir",
            str(base / "nearby"),
            "--out-dir",
            str(base / "pass2"),
        ],
        env_file=None,
    )
    assert code == 0
    manifest = json.loads(
        (base / "pass2" / "20261101-IN" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["nearby_places_sha256"]


def test_nearby_needs_the_contact_address(tmp_path, capsys):
    base = layout(tmp_path)
    code = main(args(base), environ={}, env_file=None, client_factory=quick_client)
    assert code == 1
    assert "CONTACT_EMAIL" in capsys.readouterr().err


def test_a_missing_nearby_folder_is_a_clean_error(tmp_path, capsys):
    base = layout(tmp_path)
    code = main(
        [
            "photo-select",
            "--dump-date",
            "20261101",
            "--country",
            "IN",
            "--photos-dir",
            str(base / "photos"),
            "--enrich-dir",
            str(base / "enrich"),
            "--nearby-dir",
            str(base / "nowhere"),
            "--out-dir",
            str(base / "pass2"),
        ],
        env_file=None,
    )
    assert code == 1
    assert "run nearby first" in capsys.readouterr().err
