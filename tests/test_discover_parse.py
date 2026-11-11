import pytest

from catalogue.discover.parse import (
    classify,
    extract_parent,
    iter_pages,
    normalize_title,
    parse_dump,
)
from tests.dumpfixture import page, write_dump


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Intro\n{{usablecity}}", ("city", "usable", None)),
        ("{{Guidecountry}}", ("country", "guide", None)),
        ("{{ starpark }}", ("park", "star", None)),
        ("{{outlineruralarea}}", ("ruralarea", "outline", None)),
        ("{{usablediveguide}}", ("diveguide", "usable", None)),
        ("no templates at all", (None, None, None)),
    ],
)
def test_classify_reads_the_type_and_status_template(text, expected):
    assert classify(text) == expected


def test_a_star_nomination_marker_is_not_a_type():
    assert classify("{{starnomination}}\n{{guidedistrict}}") == ("district", "guide", None)


def test_two_different_statuses_are_ambiguous_and_not_guessed():
    assert classify("{{usablecity}} {{outlinecity}}") == (None, None, "ambiguous_status")


def test_the_same_template_twice_is_not_ambiguous():
    assert classify("{{usablecity}} {{usablecity}}") == ("city", "usable", None)


def test_a_commented_out_template_is_ignored():
    assert classify("<!-- {{starcity}} -->\n{{usablecity}}") == ("city", "usable", None)


def test_templates_with_other_names_are_not_status_templates():
    assert classify("{{pagebanner|Banner.jpg}}{{see|name=Fort}}") == (None, None, None)


def test_normalize_title_follows_mediawiki_rules():
    assert normalize_title("  new_york  city ") == "New york city"
    assert normalize_title("Rio_de_Janeiro") == "Rio de Janeiro"


def test_extract_parent_reads_ispartof():
    assert extract_parent("{{IsPartOf|Rajasthan}}") == ("Rajasthan", None)


def test_extract_parent_is_case_insensitive_and_tidies_the_title():
    assert extract_parent("{{ispartof| north_india }}") == ("North india", None)


def test_the_last_ispartof_wins():
    assert extract_parent("{{IsPartOf|Fairfax County}}\n{{IsPartOf|Northern Virginia}}") == (
        "Northern Virginia",
        None,
    )


def test_a_piped_argument_is_not_guessed():
    assert extract_parent("{{IsPartOf|Finland Proper|Archipelago Sea}}") == (
        None,
        "unparsed_parent",
    )


def test_no_ispartof_gives_no_parent_and_no_problem():
    assert extract_parent("{{usablecountry}}") == (None, None)


def test_a_commented_out_ispartof_is_ignored():
    assert extract_parent("<!-- {{IsPartOf|Old}} -->{{IsPartOf|New}}") == ("New", None)


def test_parse_dump_keeps_main_namespace_articles_with_their_facts(tmp_path):
    dump = write_dump(
        tmp_path / "d.xml.bz2",
        [
            page(
                "Jaipur",
                "{{IsPartOf|Rajasthan}}\n{{usablecity}}",
                page_id=7,
                revision=555,
                ts="2026-09-30T12:00:00Z",
            )
        ],
    )

    parsed = parse_dump(dump)

    jaipur = parsed.articles["Jaipur"]
    assert (jaipur.page_id, jaipur.revision_id) == (7, 555)
    assert jaipur.timestamp == "2026-09-30T12:00:00Z"
    assert (jaipur.type, jaipur.status, jaipur.parent) == ("city", "usable", "Rajasthan")


def test_parse_dump_records_redirects_apart_from_articles(tmp_path):
    dump = write_dump(
        tmp_path / "d.xml.bz2",
        [page("Bombay", page_id=2, redirect="Mumbai"), page("Mumbai", "{{usablecity}}", page_id=3)],
    )

    parsed = parse_dump(dump)

    assert parsed.redirects == {"Bombay": "Mumbai"}
    assert "Bombay" not in parsed.articles
    assert "Mumbai" in parsed.articles


def test_parse_dump_skips_other_namespaces(tmp_path):
    dump = write_dump(
        tmp_path / "d.xml.bz2",
        [page("Talk:Jaipur", "{{usablecity}}", ns=1), page("Template:Foo", "x", ns=10)],
    )

    assert parse_dump(dump).articles == {}


def test_a_page_with_empty_text_is_kept_without_facts(tmp_path):
    dump = write_dump(tmp_path / "d.xml.bz2", [page("Empty", "", page_id=9)])

    empty = parse_dump(dump).articles["Empty"]

    assert (empty.type, empty.status, empty.parent) == (None, None, None)


def test_iter_pages_yields_articles_in_dump_order_with_text_and_skips_the_rest(tmp_path):
    dump = write_dump(
        tmp_path / "d.xml.bz2",
        [
            page("Zed", "text z", page_id=7, revision=70, ts="2026-10-02T00:00:00Z"),
            page("Bombay", page_id=2, redirect="Mumbai"),
            page("Talk:Zed", "x", page_id=8, ns=1),
            page("Alpha_page", "text a", page_id=3, revision=30),
        ],
    )

    pages = list(iter_pages(dump))

    assert [(p.title, p.page_id, p.revision_id, p.text) for p in pages] == [
        ("Zed", 7, 70, "text z"),
        ("Alpha page", 3, 30, "text a"),
    ]
    assert pages[0].timestamp == "2026-10-02T00:00:00Z"
