"""One readiness run over several countries: counts, the two-run unpublish rule and the state file
work for every country at once, and a country state is never mixed with a world state."""

import csv
import json

import pytest

from catalogue.errors import PipelineError
from tests.readinessfixture import rows, run

COUNTRIES = {1: "IN", 2: "US", 3: "JP", 5: "DE"}


def world_run(tmp_path, tag, **kw):
    return run(tmp_path, tag=tag, world=True, countries=COUNTRIES, country_iso=None, **kw)


def state_of(result):
    return json.loads((result.output_dir / "state.json").read_text(encoding="utf-8"))


def test_a_world_run_assesses_every_country_and_writes_a_world_state(tmp_path):
    result, _ = world_run(tmp_path, "1")

    assert result.totals["assessed"] == 4
    assert {r["country_iso"] for r in rows(result).values()} == {"IN", "US", "JP", "DE"}
    state = state_of(result)
    assert state["country_filter"] is None
    assert state["cities"]["1"] == {"published": True, "failed_runs": 0}  # India
    assert state["cities"]["2"] == {"published": True, "failed_runs": 0}  # United States
    by_country = {
        r["country_iso"]: r
        for r in csv.DictReader((result.output_dir / "by_country.csv").open(encoding="utf-8"))
    }
    assert by_country["IN"]["ready"] == "1" and by_country["US"]["ready"] == "1"
    assert by_country["JP"]["not_ready"] == "1" and by_country["DE"]["not_ready"] == "1"
    assert result.output_dir.name == "20261101"  # the world folder has no country suffix


def test_the_two_run_rule_unpublishes_cities_of_different_countries_in_one_run(tmp_path):
    first, _ = world_run(tmp_path, "1")
    # Jaipur (India) and Agra (United States) both lose their summary
    second, _ = world_run(tmp_path, "2", summaries=(5,), previous_folder=first.output_dir)

    assert rows(second)[1]["publish_action"] == "keep_pending_unpublish"
    assert rows(second)[2]["publish_action"] == "keep_pending_unpublish"
    assert second.totals["keep_pending_unpublish"] == 2 and second.totals["unpublish"] == 0
    assert state_of(second)["cities"]["1"] == {"published": True, "failed_runs": 1}

    third, _ = world_run(tmp_path, "3", summaries=(5,), previous_folder=second.output_dir)

    assert rows(third)[1]["publish_action"] == rows(third)[2]["publish_action"] == "unpublish"
    assert third.totals["unpublish"] == 2
    assert state_of(third)["cities"]["2"] == {"published": False, "failed_runs": 2}


def test_one_country_recovering_does_not_reset_another(tmp_path):
    first, _ = world_run(tmp_path, "1")
    second, _ = world_run(tmp_path, "2", summaries=(5,), previous_folder=first.output_dir)
    third, _ = world_run(tmp_path, "3", summaries=(2, 5), previous_folder=second.output_dir)

    assert rows(third)[2]["publish_action"] == "keep"  # the US city recovered
    assert rows(third)[1]["publish_action"] == "unpublish"  # the Indian one did not
    assert state_of(third)["cities"]["2"] == {"published": True, "failed_runs": 0}


def test_a_country_state_is_refused_by_a_world_run_and_the_other_way_round(tmp_path):
    india, _ = run(tmp_path, tag="in")  # country filter IN
    everywhere, _ = world_run(tmp_path, "all")

    with pytest.raises(PipelineError, match="country filter"):
        world_run(tmp_path, "w2", previous_folder=india.output_dir)
    with pytest.raises(PipelineError, match="country filter"):
        run(tmp_path, tag="in2", previous_folder=everywhere.output_dir)
