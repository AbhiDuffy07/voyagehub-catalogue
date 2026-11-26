"""Ride apps per city (config/ride_apps.toml): shown only where the app's own site lists the city,
or says it serves the whole country. Unknown is never shown."""

import json
from datetime import date
from pathlib import Path

import pytest

from catalogue.errors import PipelineError
from catalogue.publish.getting_around import apps_for, load_ride_apps

ROOT = Path(__file__).resolve().parent.parent
GOOD_APP = """
[[XX.apps]]
name = "Ride"
url = "https://ride.example/"
source_url = "https://ride.example/cities"
checked = 2026-11-26
coverage = "cities"
cities = ["alpha", "beta"]
"""


def _table(tmp_path, body, note='note = "Check the app."'):
    path = tmp_path / "ride_apps.toml"
    path.write_text(f"[XX]\n{note}\n{body}", encoding="utf-8")
    return load_ride_apps(path)


def test_committed_table_lists_uber_by_city_and_namma_yatri_in_bengaluru_only():
    table = load_ride_apps()
    apps = {a["name"]: a for a in table["IN"]["apps"]}
    assert apps["Uber"]["coverage"] == "cities" and "ahmedabad" in apps["Uber"]["cities"]
    assert apps["Namma Yatri"]["cities"] == frozenset({"bengaluru"})
    assert all(isinstance(a["checked"], date) for a in apps.values())
    assert all(a["source_url"].startswith("https://") for a in apps.values())


def test_ahmedabad_does_not_get_namma_yatri():
    shown = apps_for(load_ride_apps(), "IN", "ahmedabad")
    assert [a["name"] for a in shown["apps"]] == ["Uber"]
    assert all(set(a) == {"name", "url"} for a in shown["apps"])


def test_bengaluru_gets_both_apps_that_list_it():
    shown = apps_for(load_ride_apps(), "IN", "bengaluru")
    assert [a["name"] for a in shown["apps"]] == ["Uber", "Namma Yatri"]


def test_an_app_with_unknown_coverage_is_never_shown():
    table = load_ride_apps()
    unknown = [a["name"] for a in table["IN"]["apps"] if a["coverage"] == "unknown"]
    assert unknown  # Ola and Rapido give a count of cities, not a list (checked 2026-11-26)
    for slug in ("ahmedabad", "bengaluru", "delhi", "mumbai"):
        names = [a["name"] for a in apps_for(table, "IN", slug)["apps"]]
        assert not set(names) & set(unknown)


def test_a_city_no_app_lists_and_a_country_without_a_table_get_none():
    table = load_ride_apps()
    assert apps_for(table, "IN", "no-such-city") is None
    assert apps_for(table, "ZZ", "ahmedabad") is None
    assert apps_for(table, None, "ahmedabad") is None


def test_a_nationwide_app_shows_in_every_city_of_its_country(tmp_path):
    body = (
        GOOD_APP
        + """
[[XX.apps]]
name = "Everywhere"
url = "https://every.example/"
source_url = "https://every.example/about"
checked = 2026-11-26
coverage = "nationwide"
"""
    )
    table = _table(tmp_path, body)
    assert [a["name"] for a in apps_for(table, "XX", "gamma")["apps"]] == ["Everywhere"]
    assert [a["name"] for a in apps_for(table, "XX", "alpha")["apps"]] == ["Ride", "Everywhere"]
    assert apps_for(table, "XX", "gamma")["note"] == "Check the app."


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('source_url = "https://ride.example/cities"\n', ""),
        ("https://ride.example/cities", "http://ride.example/cities"),
        ("checked = 2026-11-26", 'checked = "yesterday"'),
        ('coverage = "cities"', 'coverage = "most"'),
        ('cities = ["alpha", "beta"]', "cities = []"),
        ('cities = ["alpha", "beta"]', 'cities = ["Not A Slug"]'),
        ('coverage = "cities"', 'coverage = "nationwide"'),  # nationwide with a city list
        ('url = "https://ride.example/"', 'url = "ride.example"'),
    ],
)
def test_a_damaged_entry_is_refused(tmp_path, old, new):
    assert old in GOOD_APP
    with pytest.raises(PipelineError, match="ride_apps.toml"):
        _table(tmp_path, GOOD_APP.replace(old, new))


def test_a_country_without_a_note_is_refused(tmp_path):
    with pytest.raises(PipelineError, match="ride_apps.toml"):
        _table(tmp_path, GOOD_APP, note="")


def test_every_listed_city_is_a_catalogue_slug():
    registry = json.loads((ROOT / "data" / "slugs.json").read_text(encoding="utf-8"))
    slugs = {entry["slug"] for entry in registry["entries"].values()}
    for country, entry in load_ride_apps().items():
        for app in entry["apps"]:
            missing = sorted(app["cities"] - slugs)
            assert not missing, (country, app["name"], missing)
