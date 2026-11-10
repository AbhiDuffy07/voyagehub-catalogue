import pytest

from catalogue.discover.overrides import load_country_iso_overrides
from catalogue.errors import PipelineError


def write(tmp_path, text):
    path = tmp_path / "country_iso.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_missing_file_means_no_overrides(tmp_path):
    assert load_country_iso_overrides(tmp_path / "nope.csv") == {}


def test_titles_map_to_iso_codes_and_comments_and_blanks_are_skipped(tmp_path):
    path = write(
        tmp_path,
        "country,country_iso,reason\n"
        "# explained in the reason column\n"
        "Netherlands,NL,Wikidata gives NL at deprecated rank on Q55\n"
        "\n"
        "  new_zealand ,nz,tidied\n",
    )

    assert load_country_iso_overrides(path) == {"Netherlands": "NL", "New zealand": "NZ"}


@pytest.mark.parametrize("code", ["N", "NLD", "n1", ""])
def test_a_code_that_is_not_two_letters_is_an_error(tmp_path, code):
    path = write(tmp_path, f"country,country_iso,reason\nNetherlands,{code},x\n")

    with pytest.raises(PipelineError, match="country_iso overrides"):
        load_country_iso_overrides(path)


def test_the_same_country_twice_is_an_error_not_a_silent_choice(tmp_path):
    path = write(tmp_path, "country,country_iso,reason\nPeru,PE,a\nPeru,PR,b\n")

    with pytest.raises(PipelineError, match="twice"):
        load_country_iso_overrides(path)


def test_a_file_without_the_expected_columns_is_an_error(tmp_path):
    path = write(tmp_path, "name,code\nPeru,PE\n")

    with pytest.raises(PipelineError, match="columns"):
        load_country_iso_overrides(path)
