import pytest

from catalogue.errors import PipelineError
from catalogue.photos.rules import PhotoRules, load_photo_rules


def test_committed_rules_load():
    rules = load_photo_rules()
    assert rules.version >= 1
    assert rules.category_cap == 10
    assert rules.thumb_width == 1280
    assert "Quality images" in rules.quality_categories


@pytest.mark.parametrize(
    "code",
    ["cc0", "pd", "pd-old", "cc-by-4.0", "cc-by-sa-4.0", "cc-by-2.0", "cc-by-sa-3.0-at", "fal"],
)
def test_allowed_licence_codes(code):
    assert load_photo_rules().licence_allowed(code)


@pytest.mark.parametrize(
    "code",
    ["cc-by-nc-4.0", "cc-by-nd-2.0", "cc-by-sa-1.0", "gfdl", "cc-by-4.0-nc", "copyrighted", ""],
)
def test_other_licence_codes_are_not_allowed(code):
    assert not load_photo_rules().licence_allowed(code)


def test_matching_is_case_insensitive_and_whole_string():
    rules = load_photo_rules()
    assert rules.licence_allowed("CC-BY-SA-4.0")
    assert not rules.licence_allowed("xcc0")


def test_bad_file_is_a_pipeline_error(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("rules_version = 1\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="rules.toml"):
        load_photo_rules(path)


def test_not_toml_is_a_pipeline_error(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("= =", encoding="utf-8")
    with pytest.raises(PipelineError, match="could not be read"):
        load_photo_rules(path)


def test_rules_are_a_value_object():
    assert isinstance(load_photo_rules(), PhotoRules)
