"""The committed pipeline.yml keeps its safety settings and supports a world run."""

import re
from pathlib import Path

from scripts.check_workflows import problems

PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pipeline.yml"
TEXT = PATH.read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
JOBS = ["fetch", "enrich", "summaries", "photos", "nearby", "build"]


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
        "derive-timezone draft-blocks auto-check price-bands readiness publish-static nearby"
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
        ("nearby", "nearby"),
    ):
        assert f"python -m catalogue {command}" in blocks[job]
    for network in (
        "discover",
        "enrich-wikidata",
        "wikipedia-summary",
        "photo-candidates",
        "nearby",
    ):
        assert f"python -m catalogue {network}" not in blocks["build"]


def test_the_hero_sample_runs_after_the_deploy_and_cannot_block_it():
    assert CODE.index("python -m catalogue sample-heroes") > CODE.index(
        "python -m catalogue deploy"
    )
    sample = CODE[CODE.index("Draw the hero photo review sample") :]
    assert "continue-on-error: true" in sample.split("- name:")[0]


def test_the_nearby_folder_reaches_photo_select_readiness_and_publish():
    build = job_blocks()["build"]
    # Each of these commands is continued onto a second line that names the nearby folder.
    joined = build.replace("\\\n", " ")
    for command in ("photo-select", "readiness", "publish-static"):
        line = next(ln for ln in joined.splitlines() if f"python -m catalogue {command} " in ln)
        assert "--nearby-dir out/nearby" in line, command
    nearby = job_blocks()["nearby"]
    assert nearby.index("--out-dir out/photo_selection_first") < nearby.index(
        "python -m catalogue nearby "
    )
    assert "CONTACT_EMAIL: ${{ secrets.CONTACT_EMAIL }}" in nearby
    assert "!out/cache" in nearby  # the cache stays out of the hand-over artifact


CACHE_SHA = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
CACHE_PREFIX = "nearby-cache-${{ env.DUMP_DATE }}-r${{ steps.nearby-rules.outputs.version }}-"


def _step(block, uses):
    start = block.index(f"uses: {uses}")
    return block[start : block.find("\n      - ", start) % (len(block) + 1)]


def test_nearby_restores_and_saves_its_cache_with_a_dump_and_rules_key():
    nearby = job_blocks()["nearby"]
    restore = _step(nearby, f"actions/cache/restore@{CACHE_SHA} # v6.1.0")
    save = _step(nearby, f"actions/cache/save@{CACHE_SHA} # v6.1.0")
    key = f"key: {CACHE_PREFIX}${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}"
    for step in (restore, save):
        assert "path: out/cache" in step
        assert key in step
        assert "COUNTRY" not in step and "inputs.country" not in step
    assert f"restore-keys: {CACHE_PREFIX}\n" in restore
    assert "if: always()" in save
    assert "id: nearby-rules" in nearby
    assert "load_nearby_rules().version" in nearby
    run_at = nearby.index("python -m catalogue nearby ")
    assert nearby.index("id: nearby-rules") < nearby.index(restore) < run_at < nearby.index(save)


def test_nearby_logs_free_disk_around_the_cache_so_a_world_size_cache_is_measured():
    nearby = job_blocks()["nearby"]
    restore = nearby.index("actions/cache/restore@")
    save = nearby.index("actions/cache/save@")
    disk = [m.start() for m in re.finditer(r"df -h / out", nearby)]
    assert len(disk) == 2
    assert disk[0] < restore and nearby.index("python -m catalogue nearby ") < disk[1] < save
    assert "du -sh out/cache" in nearby


def test_nearby_gets_the_job_start_time():
    nearby = job_blocks()["nearby"]
    started = nearby.index('echo "JOB_STARTED=$(date +%s)" >> "$GITHUB_ENV"')
    assert started < nearby.index("actions/setup-python")  # the first step after checkout
    line = next(ln for ln in nearby.splitlines() if "python -m catalogue nearby " in ln)
    assert started < nearby.index(line)
    assert '--started-at "$JOB_STARTED"' in line
