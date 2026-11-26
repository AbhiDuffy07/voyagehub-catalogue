import pytest

from catalogue.listings.wikitext import parse_page


def sections(text):
    return {s.key: s for s in parse_page(text).sections}


def test_the_text_before_the_first_heading_is_the_lead():
    page = parse_page("Hot and dusty. {{usablecity}}\n== Understand ==\nHistory.")

    lead, understand = page.sections
    assert (lead.key, lead.kind, lead.text) == ("lead", "lead", "Hot and dusty.")
    assert (understand.key, understand.heading, understand.kind) == (
        "understand",
        "Understand",
        "prose",
    )


def test_a_page_with_no_text_gets_no_empty_lead():
    assert [s.key for s in parse_page("== Eat ==\nRice.").sections] == ["eat"]


@pytest.mark.parametrize(
    ("heading", "key", "standard"),
    [
        ("Get in", "get_in", True),
        ("Get around", "get_around", True),
        ("Stay safe", "stay_safe", True),
        ("Stay healthy", "stay_healthy", True),
        ("Go next", "go_next", True),
        ("Other destinations", "other_destinations", True),
        ("get IN", "get_in", True),
        ("Local festivals!", "local_festivals", False),
    ],
)
def test_headings_are_normalised_and_unknown_ones_are_marked_not_standard(heading, key, standard):
    (section,) = parse_page(f"== {heading} ==\nText.").sections

    assert (section.key, section.standard) == (key, standard)
    assert section.heading == heading


@pytest.mark.parametrize("heading", ["Cities", "Regions", "Other destinations", "Districts"])
def test_destination_lists_are_marked(heading):
    (section,) = parse_page(f"== {heading} ==\n* [[Agra]]").sections

    assert section.kind == "destination_list"


def test_bullets_bold_and_subheadings_survive_as_plain_text():
    text = (
        "== Get in ==\n"
        "'''By air'''\n"
        "* [[Delhi]] has flights\n"
        "* [[Mumbai|Bombay]] too\n"
        "=== By train ===\n"
        "Trains run daily."
    )

    (section,) = parse_page(text).sections

    lines = [line.strip() for line in section.text.splitlines() if line.strip()]
    assert lines == ["By air", "Delhi has flights", "Bombay too", "By train", "Trains run daily."]


def test_listings_are_removed_from_the_text_and_counted():
    (section,) = parse_page("== See ==\nTemples are big.\n{{see|name=Fort|content=Hill.}}").sections

    assert section.text == "Temples are big."
    assert section.listing_count == 1


def test_categories_files_and_references_do_not_leak_into_the_text():
    text = (
        "== Understand ==\nA nice place.<ref>Some source</ref>\n"
        "[[Category:India]]\n[[File:Pic.jpg|thumb|A caption]]"
    )

    (section,) = parse_page(text).sections

    assert section.text == "A nice place."


def test_heading_with_formatting_is_reduced_to_its_text():
    (section,) = parse_page("== ''See'' and [[Do]] ==\nx").sections

    assert section.heading == "See and Do"


def test_a_file_or_category_link_inside_another_removed_link_does_not_crash():
    nested_file = "== A ==\n[[File:A.jpg|thumb|see [[File:B.svg|20px]] here]]\nKept text"
    nested_category = "== A ==\n[[File:A.jpg|[[Category:X]]]] Kept text"

    assert sections(nested_file)["a"].text == "Kept text"
    assert sections(nested_category)["a"].text == "Kept text"


def test_an_invisible_tag_inside_another_invisible_tag_does_not_crash():
    text = "== A ==\n<ref>outer <ref>inner</ref></ref>\nKept text"

    assert sections(text)["a"].text == "Kept text"


# --- inline templates in running text (real examples, dump 20261101) ---------------------------


def test_an_inline_listing_in_a_sentence_becomes_its_name():
    # Ahmedabad, "Get around": the published excerpt read "run by  since 1947".
    text = (
        "== Get around ==\n===By AMTS city bus===\n"
        "The local city buses are run by {{listing| name=Ahmedabad Municipal Transport Service "
        "| alt=AMTS | wikipedia=Ahmedabad Municipal Transport Service | content= | "
        "wikidata=Q4696119 }} since 1947, forming a wide network."
    )

    (section,) = parse_page(text).sections

    assert "run by Ahmedabad Municipal Transport Service since 1947, forming" in section.text
    assert section.listing_count == 1  # still counted as a listing


def test_a_listing_alone_on_its_line_stays_out_of_the_text():
    text = "== See ==\nTemples are big.\n* {{see|name=Fort|content=Hill.}}\n{{do|name=Walk}}"

    (section,) = parse_page(text).sections

    assert section.text.replace("*", "").split() == ["Temples", "are", "big."]


def test_a_marker_that_starts_a_sentence_becomes_its_name():
    # Delhi: "*{{marker|type=vicinity|name=Qila Rai Pithora|...}} — Originally founded ..."
    text = (
        "== See ==\n*{{marker|type=vicinity|name=Qila Rai Pithora|wikidata=Q14512138}} — "
        "Originally founded as Lal Kot."
    )

    (section,) = parse_page(text).sections

    assert "Qila Rai Pithora — Originally founded as Lal Kot." in section.text


@pytest.mark.parametrize(
    ("wikitext", "plain"),
    [
        # Bengaluru, "Understand"
        (
            "Summer temperatures can reach {{C|35}} and winter hovers around {{C|15}}.",
            "Summer temperatures can reach 35°C and winter hovers around 15°C.",
        ),
        # Tokyo, "Get around"
        (
            "Fares start at ¥500 for up to {{km|1.096}}, and go up every {{convert|255|m|mi}}.",
            "Fares start at ¥500 for up to 1.096 km, and go up every 255 m.",
        ),
        (
            "The lake covers {{km2|1880}} and is {{mi|1}} wide, {{ft|132}} deep.",
            "The lake covers 1880 km² and is 1 mi wide, 132 ft deep.",
        ),
        # Mumbai: a station name with a link inside the template
        (
            "Trains leave {{station|[[Chhatrapati Shivaji Maharaj Terminus]]|country=in|cr}} "
            "daily.",
            "Trains leave Chhatrapati Shivaji Maharaj Terminus daily.",
        ),
        ("Call {{phone|+91-80-1234}} to book.", "Call +91-80-1234 to book."),
        (
            "See the guide ({{ISBN|9781523834136}}) first.",
            "See the guide (ISBN 9781523834136) first.",
        ),
        ("Take line {{rint|agra|yellow}} to the fort.", "Take line to the fort."),
    ],
)
def test_inline_measures_and_names_are_written_out(wikitext, plain):
    (section,) = parse_page(f"== Get around ==\n{wikitext}").sections

    assert section.text == plain


def test_a_subheading_is_its_own_paragraph():
    text = "== Get around ==\nIntro line.\n===By bus===\nBuses run often."

    (section,) = parse_page(text).sections

    assert section.text == "Intro line.\n\nBy bus\n\nBuses run often."
