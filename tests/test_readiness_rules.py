import pytest

from catalogue.errors import PipelineError
from catalogue.readiness.rules import (
    CityFacts,
    CityState,
    assess_city,
    decide_publication,
    load_readiness_rules,
)

RULES = load_readiness_rules()


def facts(**kw):
    base = {
        "has_summary": True,
        "has_timezone": True,
        "hero_photos": 1,
        "photographed_places": 3,
        "checked_blocks": 1,
        "days_fillable": 1,
    }
    base.update(kw)
    return CityFacts(**base)


def test_the_shipped_rules_are_the_approved_thresholds():
    assert RULES.version == 1
    assert (RULES.min_hero_photos, RULES.min_photographed_places) == (1, 3)
    assert (RULES.min_checked_blocks, RULES.min_plannable_days) == (1, 1)
    assert RULES.unpublish_after_failed_runs == 2
    assert RULES.eligible_types == ("city",) and RULES.place_sections == ("see", "do")


def test_a_city_passing_every_rule_with_a_day_is_plannable():
    verdict = assess_city(facts(), RULES)
    assert (verdict.ready, verdict.tier, verdict.failed) == (True, "plannable", ())


def test_ready_without_a_whole_day_is_browse_only():
    verdict = assess_city(facts(days_fillable=0), RULES)
    assert (verdict.ready, verdict.tier) == (True, "browse_only")


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"has_summary": False}, "summary"),
        ({"has_timezone": False}, "timezone"),
        ({"hero_photos": 0}, "hero_photo"),
        ({"photographed_places": 2}, "photographed_places"),
        ({"checked_blocks": 0, "days_fillable": 0}, "checked_block"),
    ],
)
def test_each_failed_rule_makes_the_city_not_ready_and_is_named(change, rule):
    verdict = assess_city(facts(**change), RULES)
    assert (verdict.ready, verdict.tier, verdict.failed) == (False, "not_ready", (rule,))


def test_every_failed_rule_is_listed_not_just_the_first():
    verdict = assess_city(
        facts(has_summary=False, hero_photos=0, photographed_places=0, checked_blocks=0), RULES
    )
    assert verdict.failed == ("summary", "hero_photo", "photographed_places", "checked_block")


def test_a_ready_city_publishes_at_once():
    state, action = decide_publication(None, ready=True, rules=RULES)
    assert (state, action) == (CityState(published=True, failed_runs=0), "publish")


def test_a_published_ready_city_stays_published():
    state, action = decide_publication(CityState(True, 1), ready=True, rules=RULES)
    assert (state, action) == (CityState(True, 0), "keep")


def test_a_published_city_survives_one_failed_run_and_goes_after_two():
    state, action = decide_publication(CityState(True, 0), ready=False, rules=RULES)
    assert (state, action) == (CityState(True, 1), "keep_pending_unpublish")
    state, action = decide_publication(state, ready=False, rules=RULES)
    assert (state, action) == (CityState(False, 2), "unpublish")


def test_a_never_published_city_that_fails_stays_unpublished():
    state, action = decide_publication(None, ready=False, rules=RULES)
    assert (state, action) == (CityState(False, 0), "none")
    state, action = decide_publication(CityState(False, 2), ready=False, rules=RULES)
    assert (state, action) == (CityState(False, 0), "none")


def test_an_unpublished_city_that_becomes_ready_publishes_again():
    state, action = decide_publication(CityState(False, 2), ready=True, rules=RULES)
    assert (state, action) == (CityState(True, 0), "publish")


def test_a_ready_city_with_no_slug_is_never_published_and_says_why():
    state, action = decide_publication(None, ready=True, rules=RULES, has_slug=False)
    assert (state, action) == (CityState(False, 0), "blocked_no_slug")


def test_an_already_published_ready_city_stays_published_whatever_the_registry_says():
    state, action = decide_publication(CityState(True, 1), ready=True, rules=RULES, has_slug=False)
    assert (state, action) == (CityState(True, 0), "keep")


def test_a_bad_rules_file_is_one_clear_error(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text("readiness_rules_version = 1\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="r.toml"):
        load_readiness_rules(path)
