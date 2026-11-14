import pytest

from catalogue.blocks.rules import load_block_rules
from catalogue.enrich.kindmap import MAPPABLE_KINDS
from catalogue.errors import PipelineError

RULES = load_block_rules()


def test_committed_rules_load_with_version_and_reasons():
    assert RULES.version == 2
    assert RULES.travel.detour_factor == 1.3 and RULES.travel.walk_kmh == 4.5
    assert RULES.areas.walk_radius_km == 1.2
    assert (RULES.blocks.min_stops, RULES.blocks.max_stops) == (2, 4)
    assert all(RULES.reasons[n].strip() for n in ("eligible", "travel", "areas", "blocks"))


def test_every_mappable_kind_has_a_duration_with_a_reason():
    for kind in MAPPABLE_KINDS:
        low, high, basis = RULES.duration(kind, "see")
        assert 0 < low <= high and basis == "kind"
    assert all(text.strip() for text in RULES.duration_reasons.values())


def test_unknown_kind_uses_the_section_default_and_says_so():
    assert RULES.duration(None, "see")[2] == "default"
    assert RULES.duration(None, "do")[:2] == (60, 90)


def test_slots_do_not_overlap():
    assert not set(RULES.morning_kinds) & set(RULES.afternoon_kinds)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(PipelineError):
        load_block_rules(tmp_path / "nope.toml", tmp_path / "nope2.toml")


def test_a_duration_without_reason_is_refused(tmp_path):
    bad = tmp_path / "d.toml"
    bad.write_text(
        "[kinds.museum]\nmin = 1\nmax = 2\n"
        '[defaults.see]\nmin = 1\nmax = 2\nreason = "r"\n'
        '[defaults.do]\nmin = 1\nmax = 2\nreason = "r"\n',
        encoding="utf-8",
    )
    with pytest.raises(PipelineError):
        load_block_rules(durations_path=bad)


def test_a_full_day_shorter_than_a_half_day_is_refused(tmp_path):
    text = (root_rules() / "block_rules.toml").read_text(encoding="utf-8")
    bad = tmp_path / "r.toml"
    bad.write_text(
        text.replace("full_day_minutes = 420", "full_day_minutes = 100"), encoding="utf-8"
    )
    with pytest.raises(PipelineError, match="full_day_minutes"):
        load_block_rules(bad)


def root_rules():
    from catalogue.blocks.rules import BLOCK_RULES_PATH

    return BLOCK_RULES_PATH.parent


def test_subarticle_rules_load_with_a_reason():
    sub = RULES.subarticles
    assert "sight" in sub.kinds and "neighbourhood" not in sub.kinds
    assert {"region", "itinerary", "topic", "city"} <= sub.skip_types
    assert sub.parent_types == ("city",) and RULES.reasons["subarticles"].strip()


def test_unesco_sites_have_a_longer_labelled_estimate_with_a_reason():
    low, high, basis = RULES.duration("sight", "see", unesco=True)
    assert basis == "unesco" and (low, high) == RULES.unesco_duration
    assert low > RULES.duration("sight", "see")[1]
    assert low >= RULES.duration("fort_palace", "see")[1] > 0
    assert high > RULES.duration("fort_palace", "see")[1]
    assert RULES.duration_reasons["unesco"].strip()
    assert RULES.duration("sight", "see", unesco=False)[2] == "kind"


def test_a_durations_file_without_the_unesco_table_is_refused(tmp_path):
    from catalogue.blocks.rules import DURATIONS_PATH

    text = DURATIONS_PATH.read_text(encoding="utf-8")
    cut = text.index("\n[unesco]\n")
    bad = tmp_path / "d.toml"
    bad.write_text(text[:cut] + "\n" + text[text.index("[defaults.see]") :], encoding="utf-8")
    with pytest.raises(PipelineError, match="unesco"):
        load_block_rules(durations_path=bad)
