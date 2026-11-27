"""The summary shown in the app: bracketed asides gone, the first two or three sentences kept.
The stored summary stays exactly as Wikipedia returned it; only the published text changes."""

import pytest

from catalogue.errors import PipelineError
from catalogue.publish.summary_text import (
    adapted_attribution,
    display_summary,
    load_summary_rules,
    strip_brackets,
)

RULES = load_summary_rules()

# Real leads (Wikipedia via the Action API, dump 20261101 India run).
AHMEDABAD = (
    "Ahmedabad ( AH-mə-də-ba(h)d, Hindustani: [ɛʱ(ɛ).m(ə).d̪ɑː.bɑːd̪]), also spelt Amdavad "
    "(Gujarati: [ˈəmdɑʋɑd]) and historically known as Karnavati (Sanskrit: [kɐrɳaːʋɐt̪iː]), is "
    "the most populous city in the Indian state of Gujarat. It is the administrative headquarters "
    "of the Ahmedabad district and the seat of the Gujarat High Court. Ahmedabad's population of "
    "5,570,585 (per the 2011 population census) makes it the sixth-most populous city in India, "
    "and the encompassing urban area population estimated at 8,854,444 (as of 2024) is the "
    "seventh-most populous in India. Ahmedabad is located near the banks of the Sabarmati River, "
    "25 km (16 mi) from the capital of Gujarat, Gandhinagar, also known as its twin city."
)
AGRA = (
    "Agra (; Hindi: Āgarā, pronounced [ˈaːɡɾaː] ) is a city on the banks of the Yamuna river in "
    "the Indian state of Uttar Pradesh. It is a part of Western Uttar Pradesh, about 230 "
    "kilometres (140 mi) south-east of the national capital Delhi and 330 km west of the state "
    "capital Lucknow. It is also part of the Braj region. With a population of roughly 1.6 "
    "million, Agra is the fourth-most populous city in Uttar Pradesh and twenty-third most "
    "populous city in India."
)
AGARTALA = (
    "Agartala (, Bengali: [ˈagɔɾˌtɔla] , Kok Borok: [aguli]) is the capital and the largest city "
    "of the Indian state of Tripura, situated on the banks of Haora/Saidra River, about 2 "
    "kilometres (1.2 mi) east of the border with Bangladesh and about 2,499 km (1,552 mi) from "
    "the national capital, New Delhi. According to 2022 AMC data, Agartala is the second most "
    "populous city after Guwahati in Northeast India. It is India's third international internet "
    "gateway and being developed under the Smart Cities Mission."
)
AMARAVATI = (
    "Amaravati (English:  uh-mə-RAH-vuh-thi, Telugu: Amarāvati) is the capital city of the "
    "Indian state of Andhra Pradesh. It is located in Guntur district on the right bank of the "
    "Krishna River, 15 kilometres (9.3 mi) southwest of Vijayawada, 25 kilometres (15 mi) "
    "northwest of Guntur."
)


def test_committed_rules_load():
    assert (RULES.min_sentences, RULES.max_sentences, RULES.max_chars) == (2, 3, 450)


def test_a_missing_rules_file_is_refused(tmp_path):
    with pytest.raises(PipelineError):
        load_summary_rules(tmp_path / "nope.toml")


def test_ahmedabad_loses_its_pronunciations_and_census_asides():
    assert display_summary(AHMEDABAD, RULES) == (
        "Ahmedabad, also spelt Amdavad and historically known as Karnavati, is the most populous "
        "city in the Indian state of Gujarat. It is the administrative headquarters of the "
        "Ahmedabad district and the seat of the Gujarat High Court. Ahmedabad's population of "
        "5,570,585 makes it the sixth-most populous city in India, and the encompassing urban "
        "area population estimated at 8,854,444 is the seventh-most populous in India."
    )


def test_agra_keeps_three_sentences_when_they_fit():
    assert display_summary(AGRA, RULES) == (
        "Agra is a city on the banks of the Yamuna river in the Indian state of Uttar Pradesh. "
        "It is a part of Western Uttar Pradesh, about 230 kilometres south-east of the national "
        "capital Delhi and 330 km west of the state capital Lucknow. It is also part of the Braj "
        "region."
    )


def test_agartala_empty_bracket_traces_and_unit_conversions_go():
    text = display_summary(AGARTALA, RULES)
    assert text.startswith("Agartala is the capital and the largest city")
    assert "(" not in text and "[" not in text and " ," not in text
    assert "about 2 kilometres east" in text and "2,499 km from" in text


def test_amaravati_two_short_sentences_stay_whole():
    assert display_summary(AMARAVATI, RULES) == (
        "Amaravati is the capital city of the Indian state of Andhra Pradesh. It is located in "
        "Guntur district on the right bank of the Krishna River, 15 kilometres southwest of "
        "Vijayawada, 25 kilometres northwest of Guntur."
    )


@pytest.mark.parametrize(
    "text",
    [
        "St. Louis is a city in Missouri. It lies on the Mississippi. It has an arch. More.",
        "The U.S. Army built it in 1900. It was rebuilt later. It is open daily. More.",
        "J. R. R. Tolkien was born here. He moved away. He came back. More.",
        "It was founded c. 1500 by traders. It grew fast. It is big now. More.",
        "Mt. Abu is a hill station. It is cool. It is green. More.",
    ],
)
def test_abbreviations_and_initials_do_not_end_a_sentence(text):
    assert display_summary(text, RULES) == text.removesuffix(" More.")


def test_a_decimal_number_does_not_end_a_sentence():
    text = "It covers 1.6 square km. It has 3.5 million people. It is old. Fourth."
    assert display_summary(text, RULES) == (
        "It covers 1.6 square km. It has 3.5 million people. It is old."
    )


def test_a_third_sentence_that_overflows_is_left_out_but_two_always_stay():
    long_second = "Second " + "word " * 100 + "end."
    text = f"First sentence here. {long_second} Third one."
    assert display_summary(text, RULES) == f"First sentence here. {long_second}"


def test_a_single_sentence_is_kept_whole():
    one = "Only one sentence " + "word " * 120 + "end."
    assert display_summary(one, RULES) == one


def test_nested_and_square_brackets_go_but_an_unclosed_bracket_leaves_the_text_alone():
    assert strip_brackets("A (b (c) d) e [f] g.") == "A e g."
    assert strip_brackets("A (b c d e.") == "A (b c d e."
    assert strip_brackets("A b) c.") == "A b) c."


def test_attribution_says_adapted_only_when_brackets_were_removed():
    base = 'From the Wikipedia article "Agra" by Wikipedia contributors, CC BY-SA 4.0 (excerpt)'
    assert adapted_attribution(base, AGRA, display_summary(AGRA, RULES)).endswith(
        "CC BY-SA 4.0 (excerpt, adapted)"
    )
    plain = "Plain city. It is nice. It has a river. It has a fort."
    assert adapted_attribution(base, plain, display_summary(plain, RULES)) == base
    assert adapted_attribution("Other text", AGRA, "x").endswith("Other text (adapted)")


def test_published_summary_is_the_display_text_with_an_adapted_credit():
    from collections import Counter

    from catalogue.publish.build import text_of

    row = {
        "summary": AGRA,
        "attribution": 'From the Wikipedia article "Agra" by Wikipedia contributors, '
        "CC BY-SA 4.0 (excerpt)",
        "licence": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "permalink": "https://en.wikipedia.org/w/index.php?title=Agra&oldid=1",
    }
    published = text_of(row, Counter(), RULES)
    assert published["text"] == display_summary(AGRA, RULES)
    assert published["credit"]["attribution"].endswith("(excerpt, adapted)")
    assert row["summary"] == AGRA  # the stored row is not changed
