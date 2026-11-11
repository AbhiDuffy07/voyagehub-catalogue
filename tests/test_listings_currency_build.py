from catalogue.listings.currency_build import (
    build_rows,
    current_currency_qids,
    iso_code_of,
    parse_iso_exponents,
)

ISO_XML = """<ISO_4217 Pblshd="2026-09-17"><CcyTbl>
<CcyNtry><CtryNm>INDIA</CtryNm><Ccy>INR</Ccy><CcyMnrUnts>2</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>JAPAN</CtryNm><Ccy>JPY</Ccy><CcyMnrUnts>0</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>KUWAIT</CtryNm><Ccy>KWD</Ccy><CcyMnrUnts>3</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>ANTARCTICA</CtryNm><Ccy>XXX</Ccy><CcyMnrUnts>N.A.</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>FRANCE</CtryNm><CcyMnrUnts>2</CcyMnrUnts></CcyNtry>
</CcyTbl></ISO_4217>"""


def statement(qid, rank="normal", end=False):
    value = {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": qid}}}, "rank": rank}
    if end:
        value["qualifiers"] = {"P582": [{"snaktype": "value"}]}
    return value


def code_statement(code, rank="normal"):
    snak = {"snaktype": "value", "datavalue": {"value": code}}
    return {"mainsnak": snak, "rank": rank}


def test_iso_exponents_skip_entries_without_a_number_or_a_code():
    assert parse_iso_exponents(ISO_XML) == {"INR": 2, "JPY": 0, "KWD": 3}


def test_current_currencies_drop_ended_and_deprecated_statements():
    claims = {
        "P38": [statement("Q1", end=True), statement("Q2", rank="deprecated"), statement("Q3")]
    }

    assert current_currency_qids(claims) == ["Q3"]


def test_a_preferred_currency_wins_over_normal_ones():
    claims = {"P38": [statement("Q1"), statement("Q2", rank="preferred")]}

    assert current_currency_qids(claims) == ["Q2"]


def test_no_statements_means_no_currencies():
    assert current_currency_qids({}) == []


def test_the_iso_code_must_be_exactly_one_value():
    assert iso_code_of({"P498": [code_statement("INR")]}) == "INR"
    assert iso_code_of({"P498": [code_statement("INR"), code_statement("XYZ")]}) is None
    assert iso_code_of({}) is None


def test_rows_are_blank_with_a_reason_when_the_currency_cannot_be_named_for_sure():
    countries = {"IN": "Q668", "JP": "Q17", "BT": "Q917", "CU": "Q241", "ZZ": None, "KP": "Q423"}
    country_claims = {
        "Q668": {"P38": [statement("Q500")]},
        "Q17": {"P38": [statement("Q600")]},
        "Q917": {"P38": [statement("Q500"), statement("Q501")]},
        "Q241": {},
        "Q423": {"P38": [statement("Q700")]},
    }
    currency_claims = {
        "Q500": {"P498": [code_statement("INR")]},
        "Q600": {"P498": [code_statement("JPY")]},
        "Q501": {"P498": [code_statement("BTN")]},
        "Q700": {"P498": [code_statement("KPW")]},
    }
    exponents = {"INR": 2, "JPY": 0, "BTN": 2}

    rows = {
        r["country_iso"]: r
        for r in build_rows(countries, country_claims, currency_claims, exponents)
    }

    assert (rows["IN"]["currency_code"], rows["IN"]["exponent"], rows["IN"]["note"]) == (
        "INR",
        2,
        "ok",
    )
    assert (rows["JP"]["currency_code"], rows["JP"]["exponent"]) == ("JPY", 0)
    assert (rows["BT"]["currency_code"], rows["BT"]["note"]) == ("", "several_current_currencies")
    assert (rows["CU"]["currency_code"], rows["CU"]["note"]) == ("", "no_current_currency")
    assert (rows["ZZ"]["currency_code"], rows["ZZ"]["note"]) == ("", "no_wikidata_item")
    assert (rows["KP"]["currency_code"], rows["KP"]["exponent"], rows["KP"]["note"]) == (
        "KPW",
        "",
        "no_iso_exponent",
    )
    assert [
        r["country_iso"] for r in build_rows(countries, country_claims, currency_claims, exponents)
    ] == sorted(countries)


def test_the_numeric_iso_code_is_not_a_second_code():
    claims = {"P498": [code_statement("CZK"), code_statement("203")]}

    assert iso_code_of(claims) == "CZK"


def test_a_withdrawn_code_is_dropped_when_the_iso_list_does_not_have_it():
    claims = {"P498": [code_statement("PLN"), code_statement("PLZ")]}

    assert iso_code_of(claims) is None  # without the list there are two readings
    assert iso_code_of(claims, known_codes={"PLN": 2}.keys()) == "PLN"


def test_one_code_the_iso_list_lacks_is_still_returned_so_that_it_gets_no_exponent():
    assert iso_code_of({"P498": [code_statement("KPW")]}, known_codes={"INR": 2}.keys()) == "KPW"
