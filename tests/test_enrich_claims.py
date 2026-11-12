from catalogue.enrich.claims import parse_item
from tests.apifakes import (
    MOON,
    coordinate_claim,
    entity,
    item_claim,
    item_claim_with_end,
    part_of_whs_claim,
    quantity_claim,
    string_claim,
    time_qualifier,
)


def parse(claims, **kw):
    return parse_item("Q42", entity(claims, **kw))


def test_reads_labels_description_revision_and_enwiki_title():
    facts = parse(
        {}, revid=77, label="Amber Fort", description="fort in Jaipur", enwiki="Amer Fort"
    )
    assert facts.qid == "Q42"
    assert facts.revision_id == 77
    assert facts.label == "Amber Fort"
    assert facts.description == "fort in Jaipur"
    assert facts.enwiki_title == "Amer Fort"


def test_missing_optional_parts_stay_empty():
    facts = parse({})
    assert facts.label is None and facts.description is None and facts.enwiki_title is None
    assert facts.countries == () and facts.instance_of == () and facts.fees == ()
    assert facts.coordinate is None and facts.coordinate_status == "none"


def test_single_earth_coordinate():
    facts = parse({"P625": [coordinate_claim(26.98, 75.85)]})
    assert facts.coordinate_status == "ok"
    assert (facts.coordinate.lat, facts.coordinate.lon) == (26.98, 75.85)


def test_two_live_coordinates_are_multiple_and_unused():
    facts = parse({"P625": [coordinate_claim(1.0, 2.0), coordinate_claim(3.0, 4.0)]})
    assert facts.coordinate_status == "multiple"
    assert facts.coordinate is None


def test_preferred_coordinate_wins_over_normal():
    facts = parse(
        {"P625": [coordinate_claim(1.0, 2.0), coordinate_claim(3.0, 4.0, rank="preferred")]}
    )
    assert facts.coordinate_status == "ok"
    assert facts.coordinate.lat == 3.0


def test_deprecated_coordinate_is_ignored():
    facts = parse({"P625": [coordinate_claim(1.0, 2.0, rank="deprecated")]})
    assert facts.coordinate_status == "none"


def test_coordinate_on_another_globe_is_not_earth():
    facts = parse({"P625": [coordinate_claim(1.0, 2.0, globe=MOON)]})
    assert facts.coordinate_status == "not_earth"
    assert facts.coordinate is None


def test_coordinate_out_of_range_or_null_island_is_invalid():
    assert parse({"P625": [coordinate_claim(91.0, 2.0)]}).coordinate_status == "invalid"
    assert parse({"P625": [coordinate_claim(0.0, 0.0)]}).coordinate_status == "invalid"


def test_countries_instance_of_and_heritage_skip_deprecated_and_ended():
    facts = parse(
        {
            "P17": [item_claim("Q668"), item_claim("Q1", rank="deprecated")],
            "P31": [item_claim("Q570116"), item_claim_with_end("Q999")],
            "P1435": [item_claim("Q9259"), item_claim_with_end("Q8")],
        }
    )
    assert facts.countries == ("Q668",)
    assert facts.instance_of == ("Q570116",)
    assert facts.heritage == ("Q9259",)


def test_preferred_rank_beats_normal_for_item_values():
    facts = parse({"P17": [item_claim("Q1"), item_claim("Q2", rank="preferred")]})
    assert facts.countries == ("Q2",)


def test_image_whc_id_time_zone_and_iana_name():
    facts = parse(
        {
            "P18": [string_claim("Taj Mahal.jpg")],
            "P757": [string_claim("252")],
            "P421": [item_claim("Q2")],
            "P6687": [string_claim("Asia/Kolkata")],
            "P498": [string_claim("INR")],
        }
    )
    assert facts.image == "Taj Mahal.jpg"
    assert facts.whc_ids == ("252",)
    assert facts.time_zones == ("Q2",)
    assert facts.iana_zones == ("Asia/Kolkata",)
    assert facts.currency_codes == ("INR",)


def test_fee_keeps_amount_unit_and_dates_and_ended_fees_are_kept():
    facts = parse(
        {
            "P2555": [
                quantity_claim(
                    "+50", "Q80", qualifiers={"P585": time_qualifier("+2024-03-01T00:00:00Z")}
                ),
                quantity_claim(
                    "+40", None, qualifiers={"P582": time_qualifier("+2019-01-01T00:00:00Z")}
                ),
                quantity_claim("+30", "Q80"),
            ]
        }
    )
    assert [f.amount for f in facts.fees] == ["50", "40", "30"]
    first, ended, undated = facts.fees
    assert (first.unit_qid, first.point_in_time) == ("Q80", "2024-03-01")
    assert (ended.unit_qid, ended.end) == (None, "2019-01-01")
    assert (undated.point_in_time, undated.start, undated.end) == (None, None, None)


def test_unusable_snaks_do_not_crash_the_parser():
    novalue = {"rank": "normal", "mainsnak": {"snaktype": "novalue"}}
    facts = parse({"P625": [novalue], "P2555": [novalue], "P17": [novalue], "P18": [novalue]})
    assert facts.coordinate_status == "none"
    assert facts.fees == () and facts.countries == () and facts.image is None


def test_a_time_with_odd_shape_gives_no_date_not_an_error():
    broken = {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": "garbage"}}}
    facts = parse({"P2555": [quantity_claim("+5", None, qualifiers={"P585": broken})]})
    assert facts.fees[0].point_in_time is None


def test_the_parent_site_of_a_component_is_the_p361_qualifier_of_its_designation():
    facts = parse({"P1435": [part_of_whs_claim("Q10428494")]})
    assert facts.heritage == ("Q43113623",)
    assert facts.whs_parents == ("Q10428494",)


def test_several_parents_are_all_kept_sorted_and_without_duplicates():
    facts = parse(
        {"P1435": [part_of_whs_claim("Q20"), part_of_whs_claim("Q10"), part_of_whs_claim("Q20")]}
    )
    assert facts.whs_parents == ("Q10", "Q20")


def test_a_designation_without_a_parent_qualifier_has_no_parent():
    assert parse({"P1435": [part_of_whs_claim()]}).whs_parents == ()


def test_an_ended_or_deprecated_part_of_statement_gives_no_parent():
    ended = part_of_whs_claim("Q10", end="+2001-01-01T00:00:00Z")
    gone = part_of_whs_claim("Q11", rank="deprecated")
    assert parse({"P1435": [ended, gone]}).whs_parents == ()


def test_other_designations_never_give_a_parent():
    # a P361 qualifier on some other designation says nothing about a World Heritage Site
    other = part_of_whs_claim("Q10")
    other["mainsnak"]["datavalue"]["value"]["id"] = "Q17047640"
    assert parse({"P1435": [other]}).whs_parents == ()
