import pytest

from catalogue.listings.wikitext import parse_page


def listings(text):
    return [item for section in parse_page(text).sections for item in section.listings]


@pytest.mark.parametrize("kind", ["see", "do", "buy", "eat", "drink", "sleep", "go", "listing"])
def test_every_listing_kind_is_found(kind):
    found = listings(f"== Anything ==\n{{{{{kind}|name=Thing}}}}")

    assert [(item.kind, item.fields["name"]) for item in found] == [(kind, "Thing")]


def test_the_template_name_ignores_case_and_spaces():
    found = listings("== See ==\n{{ See |name=A}}\n{{EAT\n|name=B\n}}")

    assert [(i.kind, i.fields["name"]) for i in found] == [("see", "A"), ("eat", "B")]


def test_every_known_field_is_kept_exactly_and_unset_fields_are_empty():
    text = (
        "== See ==\n{{see|name=Fort|alt=Old Fort|address=1 Road|directions=by the gate"
        "|lat=26.9|long=75.8|phone=+91 1|tollfree=800|fax=+91 2|email=a@b.example"
        "|url=https://example.org|hours=9-5|price=Rs 50|checkin=1|checkout=2"
        "|wikidata=Q42|wikipedia=Fort|image=F.jpg|content=A [[fort]] on a hill."
        "|lastedit=2024-05-01}}"
    )

    (item,) = listings(text)

    assert item.fields == {
        "name": "Fort",
        "alt": "Old Fort",
        "address": "1 Road",
        "directions": "by the gate",
        "lat": "26.9",
        "long": "75.8",
        "phone": "+91 1",
        "tollfree": "800",
        "fax": "+91 2",
        "email": "a@b.example",
        "url": "https://example.org",
        "hours": "9-5",
        "price": "Rs 50",
        "checkin": "1",
        "checkout": "2",
        "wikidata": "Q42",
        "wikipedia": "Fort",
        "image": "F.jpg",
        "content": "A [[fort]] on a hill.",
        "lastedit": "2024-05-01",
        "type": "",
    }
    assert item.extra == {}


def test_missing_fields_are_stored_as_empty_strings_not_dropped():
    (item,) = listings("== Eat ==\n{{eat|name=Cafe}}")

    assert item.fields["price"] == ""
    assert item.fields["lat"] == ""
    assert set(item.fields) >= {"name", "lat", "long", "price", "hours", "wikidata", "lastedit"}


def test_unknown_parameters_go_to_extra_and_nothing_is_dropped():
    (item,) = listings("== See ==\n{{see|name=Fort|rating=5|2=positional}}")

    assert item.extra == {"rating": "5", "2": "positional"}


def test_a_listing_type_is_read_from_the_generic_listing_template_only():
    generic, specific = listings("== X ==\n{{listing|type=see|name=A}}\n{{see|name=B|type=odd}}")

    assert (generic.kind, generic.listing_type) == ("listing", "see")
    assert (specific.kind, specific.listing_type) == ("see", "")


def test_a_commented_out_listing_is_ignored():
    found = listings("== See ==\n<!-- {{see|name=Hidden}} -->\n{{see|name=Shown}}")

    assert [i.fields["name"] for i in found] == ["Shown"]


def test_a_template_inside_a_value_stays_in_the_raw_value():
    (item,) = listings("== See ==\n{{see|name=Fort|price={{INR|50}} adults}}")

    assert item.fields["price"] == "{{INR|50}} adults"


def test_a_listing_nested_in_another_template_is_found():
    found = listings("== See ==\n{{collapse|{{see|name=Inner}}}}")

    assert [i.fields["name"] for i in found] == ["Inner"]


def test_a_listing_inside_html_is_found():
    found = listings("== See ==\n<div>{{see|name=Boxed}}</div>")

    assert [i.fields["name"] for i in found] == ["Boxed"]


def test_a_listing_inside_another_listings_parameter_is_not_counted_twice():
    found = listings("== See ==\n{{see|name=Outer|content=Next to {{see|name=Inner}}.}}")

    assert [i.fields["name"] for i in found] == ["Outer"]


def test_a_repeated_parameter_follows_mediawiki_the_last_one_wins_and_is_recorded():
    (item,) = listings("== See ==\n{{see|name=First|name=Second}}")

    assert item.fields["name"] == "Second"
    assert item.repeated_params == ("name",)


def test_listings_know_the_section_they_sit_under_and_the_order_on_the_page():
    page = parse_page(
        "{{see|name=Lead listing}}\n== Eat ==\n{{eat|name=A}}\n{{eat|name=B}}\n"
        "== See ==\n{{see|name=C}}"
    )

    assert [(s.key, [i.fields["name"] for i in s.listings]) for s in page.sections] == [
        ("lead", ["Lead listing"]),
        ("eat", ["A", "B"]),
        ("see", ["C"]),
    ]


def test_markers_are_kept_apart_from_listings():
    page = parse_page(
        "== Cities ==\n{{marker|type=city|name=Agra|lat=27.1|long=78|wikidata=Q1}}\n"
        "{{see|name=Fort}}"
    )

    assert [i.fields["name"] for s in page.sections for i in s.listings] == ["Fort"]
    assert page.markers == [
        {"name": "Agra", "type": "city", "lat": "27.1", "long": "78", "wikidata": "Q1"}
    ]


def test_a_marker_inside_a_listing_is_not_a_marker():
    page = parse_page("== See ==\n{{see|name=Fort|content={{marker|name=Odd}}}}")

    assert page.markers == []
