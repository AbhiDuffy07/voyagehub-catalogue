import pytest

from catalogue.blocks.check import check_block, load_check_rules, stop_identity
from catalogue.blocks.rules import load_block_rules
from catalogue.errors import PipelineError
from tests.checkfixture import block_row, stops_for

BLOCK_RULES = load_block_rules()
RULES = load_check_rules()


def check(block, stops, reused=frozenset()):
    return check_block(block, stops, BLOCK_RULES, RULES, reused)


def sights(*names):
    return stops_for("b1", *names, kinds={n: "religious" for n in names})


def test_committed_check_rules_load_with_version_and_reasons():
    assert RULES.version == 1
    assert "stadium" in RULES.non_sight_words and "temple" in RULES.sight_words
    assert "religious" in RULES.sight_kinds and RULES.reason.strip()
    assert not set(RULES.non_sight_words) & set(RULES.sight_words)


def test_a_missing_rules_file_is_refused(tmp_path):
    with pytest.raises(PipelineError):
        load_check_rules(tmp_path / "nope.toml")


def test_a_word_in_both_lists_is_refused(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text(
        "check_rules_version = 1\n[non_sight]\nwords = ['club']\nsight_words = ['club']\n"
        "sight_kinds = ['sight']\nreason = 'r'\n",
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="both"):
        load_check_rules(bad)


def test_a_clean_block_is_checked():
    verdict = check(block_row(), sights("Amer Fort", "Panna Meena", "Jal Mahal"))
    assert (verdict.status, verdict.failures, verdict.flagged) == ("checked", [], [])


def test_one_non_sight_stop_with_two_sights_left_is_flagged_and_kept():
    stops = stops_for("b1", "Amer Fort", "Sawai Mansingh Stadium", "Panna Meena")
    stops[0]["kind"] = stops[2]["kind"] = "fort_palace"
    verdict = check(block_row(), stops)
    assert verdict.status == "flagged" and verdict.failures == ["non_sight_stop"]
    assert [(f["position"], f["word"]) for f in verdict.flagged] == [(2, "stadium")]
    assert len(stops) == 3  # nothing is removed or rewritten


def test_fewer_than_two_sights_left_is_rejected():
    stops = stops_for("b1", "Amer Fort", "Kalyani Stadium", kinds={"Amer Fort": "fort_palace"})
    verdict = check(block_row(), stops)
    assert verdict.status == "rejected" and verdict.failures == ["non_sight_stops"]
    assert [f["word"] for f in verdict.flagged] == ["stadium"]


def test_a_block_of_only_non_sights_is_rejected():
    verdict = check(block_row(), stops_for("b1", "Eros Cinema", "Serampore College"))
    assert verdict.status == "rejected" and len(verdict.flagged) == 2


@pytest.mark.parametrize(
    "name",
    [
        "Golden Temple Complex",
        "Ambaji Temple behind GMERS Hospital",
        "History Museum - Marathwada University",
        "Don Bosco Church and School",
        "Parliament Museum",
        "Madras Crocodile Bank",
        "Courtyard House",
        "Gita Bhavan",
        "Club Mahindra Fort",
        "The Mall road",  # Mussoorie and Shimla promenades are sights; mall is not on the list
        "Kala Academy",  # arts academies are cultural venues, not on the list
        "Academy of Fine Arts",
    ],
)
def test_names_with_a_sight_word_or_no_listed_word_are_not_flagged(name):
    stops = stops_for("b1", name, "Amer Fort", "Panna Meena")
    assert check(block_row(), stops).flagged == []


@pytest.mark.parametrize(
    "name,word",
    [
        ("Rajiv Gandhi Indoor Stadium", "stadium"),
        ("indian institute of management", "institute"),
        ("Jawaharlal Nehru University", "university"),
        ("Eros CINEMA", "cinema"),
        ("Bangalore Race Course", "race course"),
        ("Mahalaxmi Racecourse", "racecourse"),
        ("Madras High Court", "court"),
        ("General Post Office", "office"),
        ("Central Municipal Office Building", "office"),
        ("District Hospital", "hospital"),
        ("Hyderabad Golf Club", "golf"),
        ("Atal Sports Centre", "sports"),
        ("Binodini Theatre", "theatre"),
        ("IMAX dome theater", "theater"),
        ("Southern Railway Headquarters", "headquarters"),
        ("Palayamkottai Bus Stand", "bus stand"),
        ("Municipal Corporation Building", "municipal corporation"),
    ],
)
def test_clear_non_sight_names_with_unknown_kind_are_flagged(name, word):
    stops = stops_for("b1", name, "Amer Fort", "Panna Meena")
    stops[1]["kind"] = stops[2]["kind"] = "fort_palace"
    verdict = check(block_row(), stops)
    assert [f["word"] for f in verdict.flagged] == [word]


def test_a_known_sight_kind_or_unesco_is_never_non_sight():
    stops = stops_for("b1", "Sapna Cinema", "Test Institute", "Amer Fort")
    stops[0]["kind"] = "museum"
    stops[1]["unesco"] = True
    assert check(block_row(), stops).flagged == []


def test_a_neighbourhood_kind_does_not_protect_a_non_sight_name():
    stops = stops_for("b1", "Sports Stadium Area", "Amer Fort", "Panna Meena")
    stops[0]["kind"] = "neighbourhood"
    stops[1]["kind"] = stops[2]["kind"] = "fort_palace"
    assert [f["word"] for f in check(block_row(), stops).flagged] == ["stadium"]


def test_the_first_listed_word_is_reported_when_several_match():
    stops = stops_for("b1", "Golf Club", "Amer Fort", "Panna Meena")
    stops[1]["kind"] = stops[2]["kind"] = "fort_palace"
    words = [f["word"] for f in check(block_row(), stops).flagged]
    assert len(words) == 1 and words[0] in {"golf", "club"}
    assert words == [f["word"] for f in check(block_row(), stops).flagged]


# --- B8's guarantees, re-checked with B8's own rules -------------------------------------


@pytest.mark.parametrize("count", [1, 5])
def test_a_stop_count_outside_the_rules_is_rejected(count):
    names = [f"Fort {i}" for i in range(count)]
    verdict = check(block_row(), sights(*names))
    assert verdict.status == "rejected" and "stop_count_out_of_range" in verdict.failures


def test_a_walking_hop_over_the_limit_is_rejected():
    stops = sights("A Fort", "B Fort")
    stops[1]["hop_minutes"] = BLOCK_RULES.travel.max_walk_hop_min + 1
    verdict = check(block_row(), stops)
    assert verdict.status == "rejected" and verdict.failures == ["walk_hop_over_limit"]


def test_a_long_road_hop_is_fine():
    stops = sights("A Fort", "B Fort")
    stops[1].update(hop_method="road", hop_minutes=45.0)
    assert check(block_row(), stops).status == "checked"


def test_a_half_day_block_over_budget_is_rejected_but_a_full_day_is_not():
    over = BLOCK_RULES.blocks.half_day_minutes + 30
    assert check(block_row(minutes=over), sights("A Fort", "B Fort")).failures == [
        "half_day_over_budget"
    ]
    full = block_row(slot="full_day", slot_basis="trip", minutes=over)
    assert check(full, sights("A Fort", "B Fort")).status == "checked"


def test_a_stop_used_in_two_blocks_is_rejected():
    stops = sights("A Fort", "B Fort")
    verdict = check(block_row(), stops, reused=frozenset({stop_identity(stops[0])}))
    assert verdict.status == "rejected" and verdict.failures == ["stop_used_twice"]


def test_reasons_are_listed_once_each_in_a_fixed_order():
    stops = stops_for("b1", "Eros Cinema", "Serampore College")
    stops[1]["hop_minutes"] = 99.0
    verdict = check(block_row(minutes=999), stops, reused=frozenset({stop_identity(stops[0])}))
    assert verdict.failures == [
        "walk_hop_over_limit",
        "half_day_over_budget",
        "stop_used_twice",
        "non_sight_stops",
    ]
