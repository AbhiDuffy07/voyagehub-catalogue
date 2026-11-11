import pytest

from catalogue.errors import PipelineError
from catalogue.listings.reference import (
    COUNTRY_CURRENCY_PATH,
    DEMONYMS_PATH,
    load_country_currency,
    load_demonyms,
    load_reference,
)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_blank_rows_are_dropped_and_codes_read(tmp_path):
    path = write(
        tmp_path / "c.csv",
        "country_iso,currency_code,currency_qid,exponent,note\n"
        "IN,INR,Q1,2,ok\nBT,,,,several_current_currencies\nKP,KPW,Q2,,no_iso_exponent\n",
    )

    table = load_country_currency(path)

    assert set(table) == {"IN", "KP"}
    assert (table["IN"].code, table["IN"].exponent) == ("INR", 2)
    assert table["KP"].exponent is None


def test_an_unknown_exponent_or_a_malformed_code_is_refused(tmp_path):
    bad_code = write(
        tmp_path / "a.csv", "country_iso,currency_code,currency_qid,exponent,note\nIN,inr,Q1,2,ok\n"
    )
    bad_exponent = write(
        tmp_path / "b.csv", "country_iso,currency_code,currency_qid,exponent,note\nIN,INR,Q1,9,ok\n"
    )

    for path in (bad_code, bad_exponent):
        with pytest.raises(PipelineError):
            load_country_currency(path)


def test_demonyms_are_lowercase_words_per_country(tmp_path):
    path = write(tmp_path / "d.csv", "country_iso,demonym\nIN,Indian\nIN,bharatiya\nJP,japanese\n")

    assert load_demonyms(path) == {
        "IN": frozenset({"indian", "bharatiya"}),
        "JP": frozenset({"japanese"}),
    }


def test_the_reference_knows_every_currency_code_of_the_table_with_its_exponent(tmp_path):
    path = write(
        tmp_path / "c.csv",
        "country_iso,currency_code,currency_qid,exponent,note\n"
        "IN,INR,Q1,2,ok\nJP,JPY,Q2,0,ok\nKP,KPW,Q3,,no_iso_exponent\n",
    )
    demonyms = write(tmp_path / "d.csv", "country_iso,demonym\nIN,indian\n")

    reference = load_reference(path, demonyms)

    assert reference.exponents == {"INR": 2, "JPY": 0}
    assert reference.known_codes == {"INR", "JPY", "KPW"}


def test_the_committed_data_files_load_and_cover_india():
    reference = load_reference(COUNTRY_CURRENCY_PATH, DEMONYMS_PATH)

    assert reference.country_currency["IN"].code == "INR"
    assert reference.country_currency["JP"].exponent == 0
    assert reference.country_currency["KW"].exponent == 3
    assert "indian" in reference.demonyms["IN"]
    assert b"@" not in COUNTRY_CURRENCY_PATH.read_bytes()
    assert b"\r" not in COUNTRY_CURRENCY_PATH.read_bytes()
