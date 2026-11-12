from datetime import date

import pytest

from catalogue.enrich.claims import parse_item
from catalogue.enrich.facts import UnescoStatus, fee_summary, kind_of, timezone_of, unesco_status
from catalogue.enrich.kindmap import load_kind_map
from catalogue.errors import PipelineError
from tests.apifakes import (
    entity,
    item_claim,
    item_claim_with_end,
    part_of_whs_claim,
    quantity_claim,
    string_claim,
    time_qualifier,
)


def item(claims=None, qid="Q1"):
    return parse_item(qid, entity(claims or {}))


# --- UNESCO -------------------------------------------------------------------------------------

PART_OF = "Q43113623"  # "part of UNESCO World Heritage Site"


def test_unesco_needs_the_world_heritage_designation_and_an_id():
    both = item({"P1435": [item_claim("Q9259")], "P757": [string_claim("252")]})
    assert unesco_status(both) == UnescoStatus(True, False, (), badge="world_heritage_site")


def test_no_designation_and_no_id_is_plainly_not_unesco():
    assert unesco_status(item({})) == UnescoStatus(False, False, ())
    assert unesco_status(item({"P1435": [item_claim("Q123")]})) == UnescoStatus(False, False, ())


def test_designation_without_an_id_or_the_reverse_is_flagged_and_not_unesco():
    only_designation = item({"P1435": [item_claim("Q9259")]})
    only_id = item({"P757": [string_claim("252")]})
    assert unesco_status(only_designation) == UnescoStatus(False, False, ("unesco_inconsistent",))
    assert unesco_status(only_id) == UnescoStatus(False, False, ("unesco_inconsistent",))


def test_an_ended_designation_does_not_count():
    ended = item({"P1435": [item_claim_with_end("Q9259")], "P757": [string_claim("252")]})
    assert unesco_status(ended) == UnescoStatus(False, False, ("unesco_inconsistent",))


def test_unknown_item_is_not_unesco_and_not_flagged():
    assert unesco_status(None) == UnescoStatus(False, False, ())


PARENT = "Q10428494"


def parent_site(label="Hill Forts of Rajasthan", whs=True, qid=PARENT):
    claims = {"P1435": [item_claim("Q9259")]} if whs else {}
    return {qid: parse_item(qid, entity(claims, label=label))}


def component(*parents, **extra):
    claims = {"P1435": [part_of_whs_claim(q) for q in parents] or [part_of_whs_claim()], **extra}
    return item(claims)


def test_a_component_is_marked_apart_with_its_parent_site_id_and_label():
    status = unesco_status(component(PARENT), parent_site())
    assert status == UnescoStatus(
        False, True, (), PARENT, "Hill Forts of Rajasthan", "part_of_world_heritage_site"
    )


def test_a_component_may_carry_a_world_heritage_id_without_a_flag():
    status = unesco_status(component(PARENT, P757=[string_claim("247rev-005")]), parent_site())
    assert (status.is_unesco, status.component, status.flags) == (False, True, ())


def test_a_component_without_a_parent_qualifier_is_flagged_and_has_no_parent():
    status = unesco_status(component(), parent_site())
    assert status == UnescoStatus(False, True, ("unesco_parent_missing",), None, None)


def test_a_component_with_two_parents_is_flagged_and_none_is_picked():
    status = unesco_status(component(PARENT, "Q4527"), parent_site())
    assert status == UnescoStatus(False, True, ("unesco_parent_ambiguous",), None, None)


def test_a_parent_that_is_not_itself_a_world_heritage_site_is_flagged_but_kept():
    status = unesco_status(component(PARENT), parent_site(whs=False))
    assert status.flags == ("unesco_parent_not_whs",)
    assert (status.parent_qid, status.parent_label) == (PARENT, "Hill Forts of Rajasthan")


def test_a_parent_that_was_not_fetched_is_flagged_with_no_label():
    status = unesco_status(component(PARENT), {})
    assert status.flags == ("unesco_parent_not_whs",)
    assert (status.parent_qid, status.parent_label) == (PARENT, None)


def test_an_item_that_is_both_the_site_and_marked_part_of_is_the_site():
    both = item(
        {
            "P1435": [item_claim("Q9259"), part_of_whs_claim(PARENT)],
            "P757": [string_claim("247")],
        }
    )
    assert unesco_status(both, parent_site()) == UnescoStatus(
        True, False, (), badge="world_heritage_site"
    )


def test_an_ended_part_of_designation_is_not_a_component():
    ended = item({"P1435": [part_of_whs_claim(PARENT, end="+2001-01-01T00:00:00Z")]})
    assert unesco_status(ended, parent_site()) == UnescoStatus(False, False, ())


def test_parents_are_looked_up_only_when_given_and_without_them_there_is_no_badge():
    status = unesco_status(component(PARENT))
    assert status.parent_qid == PARENT and status.badge is None


def test_no_badge_unless_a_verified_parent_site_is_named():
    # The badge always needs the parent site's name: "Part of UNESCO World Heritage Site: <site>".
    assert unesco_status(component(), parent_site()).badge is None  # no parent
    assert unesco_status(component(PARENT, "Q4527"), parent_site()).badge is None  # two parents
    assert unesco_status(component(PARENT), parent_site(whs=False)).badge is None  # not a site
    assert unesco_status(component(PARENT), {}).badge is None  # parent not fetched
    assert unesco_status(component(PARENT), parent_site()).badge == "part_of_world_heritage_site"


def test_inconsistent_items_get_no_badge():
    only_designation = item({"P1435": [item_claim("Q9259")]})
    assert unesco_status(only_designation).badge is None


# --- kind ---------------------------------------------------------------------------------------

KIND_MAP = {"Q1": "museum", "Q2": "museum", "Q3": "religious"}


def test_kind_from_one_mapped_class():
    assert kind_of(item({"P31": [item_claim("Q1")]}), KIND_MAP) == ("museum", ())


def test_two_classes_with_the_same_kind_agree():
    assert kind_of(item({"P31": [item_claim("Q1"), item_claim("Q2")]}), KIND_MAP) == ("museum", ())


def test_classes_with_different_kinds_are_ambiguous_and_give_none():
    result = kind_of(item({"P31": [item_claim("Q1"), item_claim("Q3")]}), KIND_MAP)
    assert result == (None, ("kind_ambiguous",))


def test_unmapped_classes_give_no_kind_and_no_flag():
    assert kind_of(item({"P31": [item_claim("Q99")]}), KIND_MAP) == (None, ())
    assert kind_of(item({}), KIND_MAP) == (None, ())
    assert kind_of(None, KIND_MAP) == (None, ())


def test_a_mapped_class_next_to_unmapped_ones_still_gives_its_kind():
    assert kind_of(item({"P31": [item_claim("Q99"), item_claim("Q3")]}), KIND_MAP)[0] == "religious"


# --- kind map file ------------------------------------------------------------------------------


def write(tmp_path, text):
    path = tmp_path / "kind_map.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_kind_map_loads_and_inverts(tmp_path):
    path = write(tmp_path, '[kinds]\nmuseum = ["Q33506"]\nreligious = ["Q842402", "Q32815"]\n')
    assert load_kind_map(path) == {
        "Q33506": "museum",
        "Q842402": "religious",
        "Q32815": "religious",
    }


def test_an_empty_kind_map_is_allowed(tmp_path):
    assert load_kind_map(write(tmp_path, "[kinds]\n")) == {}


@pytest.mark.parametrize(
    "text",
    [
        '[kinds]\nmuseum = ["Q1"]\nsight = ["Q1"]\n',  # one class under two kinds
        '[kinds]\nother = ["Q1"]\n',  # "other" is what you get by not mapping
        '[kinds]\nzoo = ["Q1"]\n',  # not a place kind
        '[kinds]\nmuseum = ["q1"]\n',  # malformed Q-ID
        '[kinds]\nmuseum = "Q1"\n',  # not a list
        "not toml [",
    ],
)
def test_a_bad_kind_map_is_refused(tmp_path, text):
    with pytest.raises(PipelineError):
        load_kind_map(write(tmp_path, text))


def test_the_committed_kind_map_loads():
    from catalogue.enrich.kindmap import KIND_MAP_PATH

    load_kind_map(KIND_MAP_PATH)


# --- time zone ----------------------------------------------------------------------------------


def zone_item(qid, iana):
    return parse_item(qid, entity({"P6687": [string_claim(z) for z in iana]}))


def test_zone_from_the_places_own_single_time_zone_item():
    place = item({"P421": [item_claim("Q2")]})
    zones = {"Q2": zone_item("Q2", ["Asia/Kolkata"])}
    result = timezone_of(place, zones, None)
    assert (result.name, result.basis, result.flags) == ("Asia/Kolkata", "wikidata_item", ())


def test_two_zone_items_are_ambiguous_and_use_no_fallback():
    place = item({"P421": [item_claim("Q2"), item_claim("Q3")]})
    zones = {"Q2": zone_item("Q2", ["Asia/Kolkata"]), "Q3": zone_item("Q3", ["Asia/Dhaka"])}
    country = item({"P421": [item_claim("Q2")]}, qid="Q668")
    result = timezone_of(place, zones, country)
    assert result.name is None and result.flags == ("timezone_ambiguous",)


def test_one_zone_item_with_two_iana_names_is_ambiguous():
    place = item({"P421": [item_claim("Q2")]})
    zones = {"Q2": zone_item("Q2", ["Asia/Kolkata", "Asia/Calcutta"])}
    assert timezone_of(place, zones, None).flags == ("timezone_ambiguous",)


def test_fallback_to_the_countrys_single_zone_is_labelled():
    place = item({})
    country = item({"P421": [item_claim("Q2")]}, qid="Q668")
    zones = {"Q2": zone_item("Q2", ["Asia/Kolkata"])}
    result = timezone_of(place, zones, country)
    assert (result.name, result.basis) == ("Asia/Kolkata", "country_single_zone")
    assert result.flags == ()


def test_a_country_with_several_zones_gives_no_fallback():
    country = item({"P421": [item_claim("Q2"), item_claim("Q3")]}, qid="Q159")
    zones = {"Q2": zone_item("Q2", ["Europe/Moscow"]), "Q3": zone_item("Q3", ["Asia/Omsk"])}
    result = timezone_of(item({}), zones, country)
    assert result.name is None and result.flags == ("timezone_unknown",)


def test_a_zone_item_without_an_iana_name_is_unknown_not_guessed():
    place = item({"P421": [item_claim("Q2")]})
    result = timezone_of(place, {"Q2": zone_item("Q2", [])}, None)
    assert result.name is None and result.flags == ("timezone_unknown",)


def test_a_zone_item_that_was_not_fetched_is_unknown():
    place = item({"P421": [item_claim("Q2")]})
    assert timezone_of(place, {}, None).flags == ("timezone_unknown",)


# --- entry fees (counted, never used) -----------------------------------------------------------

DATA_DATE = date(2026, 11, 1)


def fee_item(*statements):
    return item({"P2555": list(statements)})


def inr_units():
    return {"Q80": parse_item("Q80", entity({"P498": [string_claim("INR")]}))}


def test_no_fee_statement():
    summary = fee_summary(item({}), {}, DATA_DATE)
    assert (summary.has_fee, summary.dated, summary.within_two_years) == (False, False, False)


def test_an_undated_fee_is_counted_but_not_dated():
    summary = fee_summary(fee_item(quantity_claim("+50", "Q80")), inr_units(), DATA_DATE)
    assert summary.has_fee and not summary.dated and not summary.within_two_years
    assert summary.currencies == ("INR",)


def test_a_fee_dated_within_two_years():
    claim = quantity_claim(
        "+50", "Q80", qualifiers={"P585": time_qualifier("+2025-03-01T00:00:00Z")}
    )
    summary = fee_summary(fee_item(claim), inr_units(), DATA_DATE)
    assert summary.dated and summary.within_two_years


def test_a_fee_dated_more_than_two_years_ago_is_dated_but_not_fresh():
    claim = quantity_claim(
        "+50", "Q80", qualifiers={"P585": time_qualifier("+2023-10-31T00:00:00Z")}
    )
    summary = fee_summary(fee_item(claim), inr_units(), DATA_DATE)
    assert summary.dated and not summary.within_two_years


def test_exactly_two_years_back_still_counts_as_within():
    claim = quantity_claim(
        "+50", "Q80", qualifiers={"P585": time_qualifier("+2024-11-01T00:00:00Z")}
    )
    assert fee_summary(fee_item(claim), inr_units(), DATA_DATE).within_two_years


def test_the_freshest_date_across_statements_decides():
    old = quantity_claim("+40", "Q80", qualifiers={"P585": time_qualifier("+2010-01-01T00:00:00Z")})
    new = quantity_claim("+60", "Q80", qualifiers={"P580": time_qualifier("+2026-01-01T00:00:00Z")})
    assert fee_summary(fee_item(old, new), inr_units(), DATA_DATE).within_two_years


def test_a_unit_that_is_not_a_known_currency_gives_no_currency():
    summary = fee_summary(fee_item(quantity_claim("+50", "Q999")), inr_units(), DATA_DATE)
    assert summary.has_fee and summary.currencies == ()
    assert fee_summary(fee_item(quantity_claim("+50")), {}, DATA_DATE).currencies == ()


def test_a_date_in_the_future_is_not_within_two_years_of_the_past():
    claim = quantity_claim(
        "+50", "Q80", qualifiers={"P585": time_qualifier("+2031-01-01T00:00:00Z")}
    )
    summary = fee_summary(fee_item(claim), inr_units(), DATA_DATE)
    assert summary.dated and not summary.within_two_years


def test_a_year_only_wikidata_date_counts_as_not_dated():
    # Wikidata writes "+2013-00-00T00:00:00Z" for "2013"; it is not a day, so it is not a date.
    claim = quantity_claim(
        "+50", "Q80", qualifiers={"P585": time_qualifier("+2013-00-00T00:00:00Z")}
    )
    summary = fee_summary(fee_item(claim), inr_units(), DATA_DATE)
    assert summary.has_fee and not summary.dated and not summary.within_two_years
