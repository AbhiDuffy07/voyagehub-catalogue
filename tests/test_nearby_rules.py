import pytest

from catalogue.errors import PipelineError
from catalogue.nearby.rules import MAX_SEARCH_RADIUS_M, load_nearby_rules

GOOD = """
rules_version = 3
[geosearch]
enabled = true
search_radius_m = 100
name_free_radius_m = 40
max_unnamed_files = 3
ambiguity_margin_m = 50
max_photos_per_place = 3
result_limit = 50
reason = "why"
[places]
radius_km = 8
result_limit = 500
kinds = ["sight", "museum"]
max_per_city = 10
same_place_km = 0.15
any_name_km = 0.04
reason = "why"
[cache]
max_age_days = 30
"""


def write(tmp_path, text):
    path = tmp_path / "nearby.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_rules_load():
    rules = load_nearby_rules()
    assert rules.version >= 1
    assert 0 < rules.search_radius_m <= MAX_SEARCH_RADIUS_M
    assert 0 <= rules.name_free_radius_m <= rules.search_radius_m
    assert rules.places_radius_km <= 10
    assert "museum" in rules.kinds


def test_a_good_file_loads_every_value(tmp_path):
    rules = load_nearby_rules(write(tmp_path, GOOD))
    assert rules.version == 3
    assert rules.geosearch_enabled is True
    assert rules.search_radius_m == 100 and rules.name_free_radius_m == 40
    assert rules.max_unnamed_files == 3 and rules.ambiguity_margin_m == 50
    assert rules.max_photos_per_place == 3 and rules.geosearch_limit == 50
    assert rules.places_radius_km == 8 and rules.places_limit == 500
    assert rules.kinds == frozenset({"sight", "museum"})
    assert rules.max_per_city == 10
    assert rules.same_place_km == 0.15 and rules.any_name_km == 0.04
    assert rules.cache_max_age_days == 30


def test_name_free_radius_zero_is_allowed(tmp_path):
    rules = load_nearby_rules(
        write(tmp_path, GOOD.replace("name_free_radius_m = 40", "name_free_radius_m = 0"))
    )
    assert rules.name_free_radius_m == 0


def test_geosearch_can_be_switched_off(tmp_path):
    rules = load_nearby_rules(write(tmp_path, GOOD.replace("enabled = true", "enabled = false")))
    assert rules.geosearch_enabled is False


@pytest.mark.parametrize(
    "old, new",
    [
        ("search_radius_m = 100", "search_radius_m = 151"),
        ("search_radius_m = 100", "search_radius_m = 0"),
        ("name_free_radius_m = 40", "name_free_radius_m = 120"),
        ("name_free_radius_m = 40", "name_free_radius_m = -1"),
        ("radius_km = 8", "radius_km = 11"),
        ("result_limit = 500", "result_limit = 501"),
        ("result_limit = 50\n", "result_limit = 501\n"),
        ('kinds = ["sight", "museum"]', 'kinds = ["sight", "hotel"]'),
        ('kinds = ["sight", "museum"]', "kinds = []"),
        ("max_per_city = 10", "max_per_city = true"),
        ("rules_version = 3", 'rules_version = "3"'),
        ('reason = "why"\n[places]', "[places]"),
        ("max_age_days = 30", "max_age_days = 0"),
        ("enabled = true", 'enabled = "no"'),
        ("enabled = true", "enabled = 1"),
        ("enabled = true\n", ""),
    ],
)
def test_bad_values_are_refused_with_the_file_name(tmp_path, old, new):
    assert old in GOOD
    with pytest.raises(PipelineError) as raised:
        load_nearby_rules(write(tmp_path, GOOD.replace(old, new, 1)))
    assert "nearby.toml" in str(raised.value)


def test_an_unreadable_file_is_a_safe_error(tmp_path):
    with pytest.raises(PipelineError):
        load_nearby_rules(write(tmp_path, "this is = = not toml"))
