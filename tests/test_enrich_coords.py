import pytest

from catalogue.enrich.claims import ItemFacts, parse_item
from catalogue.enrich.coords import country_check, decide_coordinate
from catalogue.enrich.rules import ENRICH_RULES_PATH, load_enrich_rules
from catalogue.errors import PipelineError
from catalogue.listings.quality import distance_km
from tests.apifakes import coordinate_claim, entity, item_claim

JAIPUR = (26.92, 75.82)


def item(claims=None, qid="Q1") -> ItemFacts:
    return parse_item(qid, entity(claims or {}))


def with_point(lat=26.99, lon=75.85, **extra):
    return item({"P625": [coordinate_claim(lat, lon)], **extra})


def decide(**kw):
    defaults = dict(
        own_point=None,
        own_invalid=False,
        item=with_point(),
        item_problem=None,
        country="ok",
        reference_point=JAIPUR,
        limit_km=100.0,
        needs_reference=True,
        is_destination_item=False,
        disagreement_km=25.0,
        max_precision=0.02,
    )
    return decide_coordinate(**{**defaults, **kw})


# --- own point wins ---------------------------------------------------------------------------


def test_a_usable_own_point_is_never_replaced():
    result = decide(own_point=(26.93, 75.83))
    assert not result.filled and result.lat is None
    assert result.flags == ()


def test_own_point_and_a_far_wikidata_point_are_flagged_not_changed():
    result = decide(own_point=(10.0, 10.0))
    assert not result.filled
    assert result.flags == ("wikidata_coord_disagrees",)
    assert result.disagreement_km and result.disagreement_km > 25
    assert (result.wikidata_lat, result.wikidata_lon) == (26.99, 75.85)


def test_own_point_near_the_wikidata_point_has_no_flag_but_keeps_the_distance():
    result = decide(own_point=(26.99, 75.86))
    assert result.flags == () and result.disagreement_km is not None


# --- filling ------------------------------------------------------------------------------------


def test_a_clean_wikidata_point_fills_a_missing_one():
    result = decide()
    assert result.filled and result.source == "wikidata"
    assert (result.lat, result.lon) == (26.99, 75.85)
    assert result.flags == ()


def test_an_invalid_own_geo_counts_as_none_and_is_flagged():
    result = decide(own_invalid=True)
    assert result.filled
    assert result.flags == ("own_geo_invalid",)


def test_no_page_point_fills_only_with_a_confirmed_country_and_says_so():
    result = decide(reference_point=None)
    assert result.filled and result.flags == ("no_page_point",)


def test_a_destination_needs_no_reference_point_and_gets_no_such_flag():
    result = decide(reference_point=None, limit_km=None, needs_reference=False)
    assert result.filled and result.flags == ()


def test_a_page_type_without_a_limit_is_filled_but_says_it_was_not_distance_checked():
    result = decide(limit_km=None, own_point=None, reference_point=(10.0, 10.0))
    assert result.filled and result.flags == ("no_far_limit",)


# --- refusals: the coordinate stays empty, the Wikidata value is kept -----------------------------


def test_a_far_wikidata_point_is_not_filled():
    result = decide(item=with_point(10.0, 10.0))
    assert not result.filled and result.flags == ("wikidata_coord_far",)
    assert (result.wikidata_lat, result.wikidata_lon) == (10.0, 10.0)


def test_the_limit_is_inclusive():
    # exactly on the limit is allowed; only beyond it is "far"
    point = (26.99, 75.85)
    exact = distance_km(JAIPUR, point)
    assert decide(item=with_point(*point), limit_km=exact).filled
    assert not decide(item=with_point(*point), limit_km=exact - 0.001).filled


def test_a_disagreeing_country_blocks_the_fill():
    result = decide(country="disagrees")
    assert not result.filled and result.flags == ("wikidata_country_disagrees",)


def test_an_unknown_country_blocks_the_fill():
    result = decide(country="unknown")
    assert not result.filled and result.flags == ("country_unconfirmed",)


def test_the_cities_own_item_is_never_used_for_a_listing():
    result = decide(is_destination_item=True)
    assert not result.filled and result.flags == ("item_is_destination",)


def test_several_blocking_reasons_are_all_reported_in_a_fixed_order():
    result = decide(item=with_point(10.0, 10.0), country="disagrees", is_destination_item=True)
    assert not result.filled
    assert result.flags == (
        "item_is_destination",
        "wikidata_country_disagrees",
        "wikidata_coord_far",
    )


@pytest.mark.parametrize(
    ("claims", "flag"),
    [
        ({}, "no_p625"),
        (
            {"P625": [coordinate_claim(1.0, 2.0), coordinate_claim(3.0, 4.0)]},
            "multiple_coordinates",
        ),
        (
            {"P625": [coordinate_claim(1.0, 2.0, globe="http://www.wikidata.org/entity/Q405")]},
            "not_earth",
        ),
        ({"P625": [coordinate_claim(95.0, 2.0)]}, "invalid_wikidata_coordinate"),
    ],
)
def test_coordinate_problems_on_the_item(claims, flag):
    result = decide(item=item(claims))
    assert not result.filled and result.flags == (flag,)
    assert result.wikidata_lat is None


@pytest.mark.parametrize(
    "problem", ["no_wikidata_id", "bad_wikidata_id", "item_missing", "item_merged"]
)
def test_an_item_problem_is_reported_and_nothing_is_filled(problem):
    result = decide(item=None, item_problem=problem)
    assert not result.filled and result.flags == (problem,)


def test_an_item_problem_is_ignored_when_the_page_already_has_a_point():
    result = decide(item=None, item_problem="item_missing", own_point=(26.9, 75.8))
    assert result.flags == ()


# --- the country check ------------------------------------------------------------------------


def test_country_check_ok_disagrees_unknown():
    acceptable = frozenset({"Q668"})
    assert country_check(item({"P17": [item_claim("Q668")]}), acceptable) == "ok"
    assert country_check(item({"P17": [item_claim("Q30")]}), acceptable) == "disagrees"
    assert country_check(item({}), acceptable) == "unknown"
    assert country_check(None, acceptable) == "unknown"
    assert country_check(item({"P17": [item_claim("Q668")]}), frozenset()) == "unknown"


def test_a_territory_item_naming_its_sovereign_state_is_accepted_when_the_caller_allows_it():
    # French Polynesia is Q30971; Wikidata says France (Q142) for Bora Bora.
    acceptable = frozenset({"Q30971", "Q142"})
    assert country_check(item({"P17": [item_claim("Q142")]}), acceptable) == "ok"


# --- the rules file -----------------------------------------------------------------------------


def test_the_committed_rules_file_loads_with_a_reason_for_every_value():
    rules = load_enrich_rules(ENRICH_RULES_PATH)
    assert isinstance(rules.version, int)
    assert rules.disagreement_km > 0
    assert rules.reasons["disagreement_km"].strip()


def test_a_rules_file_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("rules_version = 1\n[coordinates.disagreement]\nkm = 10\n", encoding="utf-8")
    with pytest.raises(PipelineError):
        load_enrich_rules(path)


def test_a_rules_file_with_a_bad_version_or_km_is_refused(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(
        'rules_version = "1"\n[coordinates.disagreement]\nkm = 10\nreason = "x"\n', encoding="utf-8"
    )
    with pytest.raises(PipelineError):
        load_enrich_rules(path)
    path.write_text(
        'rules_version = 1\n[coordinates.disagreement]\nkm = -3\nreason = "x"\n', encoding="utf-8"
    )
    with pytest.raises(PipelineError):
        load_enrich_rules(path)
    path.write_text("not toml [", encoding="utf-8")
    with pytest.raises(PipelineError):
        load_enrich_rules(path)


# --- precision ----------------------------------------------------------------------------------


def with_precision(precision):
    claim = coordinate_claim(26.99, 75.85)
    claim["mainsnak"]["datavalue"]["value"]["precision"] = precision
    return item({"P625": [claim]})


def test_a_degree_level_coordinate_is_not_used_and_says_so():
    result = decide(item=with_precision(1.0))
    assert not result.filled and result.flags == ("wikidata_coord_coarse",)
    assert (result.wikidata_lat, result.wikidata_lon) == (26.99, 75.85)


def test_precision_exactly_at_the_limit_is_allowed():
    assert decide(item=with_precision(0.02)).filled
    assert not decide(item=with_precision(0.0200001)).filled


def test_a_fine_or_unknown_precision_is_allowed():
    assert decide(item=with_precision(1e-05)).filled
    assert decide(item=with_precision(None)).filled


def test_coarse_comes_after_the_country_flags_and_before_far():
    result = decide(item=with_precision(1.0), country="disagrees", reference_point=(10.0, 10.0))
    assert result.flags == (
        "wikidata_country_disagrees",
        "wikidata_coord_coarse",
        "wikidata_coord_far",
    )


def test_precision_is_not_checked_when_the_page_has_its_own_point():
    result = decide(item=with_precision(1.0), own_point=(26.99, 75.85))
    assert result.flags == ()


def test_the_precision_limit_is_in_the_rules_file_with_a_reason():
    rules = load_enrich_rules(ENRICH_RULES_PATH)
    assert 0 < rules.max_precision_degrees < 1
    assert rules.reasons["max_precision_degrees"].strip()


def test_a_rules_file_without_a_precision_rule_is_refused(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(
        'rules_version = 2\n[coordinates.disagreement]\nkm = 10\nreason = "x"\n',
        encoding="utf-8",
    )
    with pytest.raises(PipelineError):
        load_enrich_rules(path)
