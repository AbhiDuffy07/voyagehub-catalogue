"""The committed pipeline.yml keeps its safety settings and supports a world run."""

import re
from pathlib import Path

from scripts.check_workflows import problems

PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pipeline.yml"
TEXT = PATH.read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
JOBS = ["fetch", "enrich", "summaries", "photos", "build"]


def job_blocks():
    parts = re.split(r"^  (\w+):\n", TEXT.split("\njobs:\n", 1)[1], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_the_workflow_passes_the_repo_guard():
    assert problems("pipeline.yml", TEXT) == []


def test_the_jobs_run_one_after_another_in_a_fixed_order():
    blocks = job_blocks()

    assert list(blocks) == JOBS
    for earlier, later in zip(JOBS, JOBS[1:], strict=False):
        assert re.search(rf"^    needs: {earlier}$", blocks[later], flags=re.M)


def test_every_job_has_a_timeout_under_the_six_hour_limit_and_the_production_environment():
    for name, block in job_blocks().items():
        minutes = re.search(r"^    timeout-minutes: (\d+)$", block, flags=re.M)
        assert minutes and int(minutes.group(1)) <= 345, name
        assert re.search(r"^    environment: production$", block, flags=re.M), name


def test_deploy_is_off_by_default_and_needs_the_confirmed_flag():
    deploy = re.search(r"      deploy:\n(?:        .*\n)+", TEXT).group(0)

    assert "default: false" in deploy
    assert "if: inputs.deploy" in TEXT and "--yes" in TEXT


def test_country_accepts_all_or_two_capitals_and_all_means_no_country_flag():
    assert "^([A-Z]{2}|ALL)$" in CODE
    country_flag = [ln.strip() for ln in CODE.splitlines() if '--country "$COUNTRY"' in ln]
    # the only place the flag is built is the line that leaves it out for ALL
    assert country_flag
    assert all(ln.startswith('args=(); [ "$COUNTRY" = "ALL" ] || args=(') for ln in country_flag)
    needs_country = (
        "fetch-listings enrich-wikidata wikipedia-summary photo-candidates photo-select "
        "derive-timezone draft-blocks auto-check price-bands readiness publish-static"
    ).split()
    for line in CODE.splitlines():
        for command in needs_country:
            if f"python -m catalogue {command} " in line:
                assert '"${args[@]}"' in line, line


def test_the_state_artifact_is_per_country_filter_and_all_has_its_own():
    assert "readiness-state-${{ inputs.country }}" in TEXT
    assert "readiness-state-$COUNTRY" in TEXT
    assert "out/readiness/${{ env.FOLDER }}/state.json" in TEXT
    assert "format('{0}-{1}', inputs.dump_date, inputs.country)" in TEXT


def test_secrets_are_never_printed():
    assert "set -x" not in CODE
    assert not re.search(r"echo[^\n]*secrets\.", CODE)
    assert not re.search(r"^\s*(run:\s*)?(env|printenv)\s*$", CODE, flags=re.M)


def test_each_network_stage_has_its_own_job_and_the_build_job_has_no_wikimedia_stage():
    blocks = job_blocks()

    for job, command in (
        ("fetch", "discover"),
        ("enrich", "enrich-wikidata"),
        ("summaries", "wikipedia-summary"),
        ("photos", "photo-candidates"),
    ):
        assert f"python -m catalogue {command}" in blocks[job]
    for network in ("discover", "enrich-wikidata", "wikipedia-summary", "photo-candidates"):
        assert f"python -m catalogue {network}" not in blocks["build"]


def test_the_hero_sample_runs_after_the_deploy_and_cannot_block_it():
    assert CODE.index("python -m catalogue sample-heroes") > CODE.index(
        "python -m catalogue deploy"
    )
    sample = CODE[CODE.index("Draw the hero photo review sample") :]
    assert "continue-on-error: true" in sample.split("- name:")[0]
