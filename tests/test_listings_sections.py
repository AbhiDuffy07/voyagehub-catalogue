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
