from pathlib import Path

from scripts.check_workflows import check_directory, problems

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
CLEAN = f"""name: ci
on:
  pull_request:
permissions:
  contents: read
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{SHA} # v7.0.1
      - run: echo hello
"""


def test_a_clean_workflow_has_no_problems():
    assert problems("ci.yml", CLEAN) == []


def test_pull_request_target_is_flagged():
    text = CLEAN.replace("pull_request:", "pull_request_target:")

    assert any("pull_request_target" in problem for problem in problems("ci.yml", text))


def test_an_action_pinned_to_a_tag_is_flagged():
    text = CLEAN.replace(f"@{SHA} # v7.0.1", "@v7")

    assert any("pinned" in problem for problem in problems("ci.yml", text))


def test_an_action_pinned_to_a_branch_or_short_sha_is_flagged():
    for ref in ("main", SHA[:7]):
        text = CLEAN.replace(f"@{SHA} # v7.0.1", f"@{ref}")

        assert problems("ci.yml", text), ref


def test_a_commented_out_unpinned_action_is_ignored():
    text = CLEAN + "      # - uses: actions/checkout@v4\n"

    assert problems("ci.yml", text) == []


def test_a_local_action_path_is_allowed():
    text = CLEAN + "      - uses: ./.github/actions/thing\n"

    assert problems("ci.yml", text) == []


def test_the_ci_workflow_must_not_use_secrets():
    text = CLEAN + "      - run: echo ${{ secrets.DATABASE_URL }}\n"

    assert any("secrets" in problem for problem in problems("ci.yml", text))


def test_other_workflows_may_use_secrets():
    text = CLEAN + "      - run: echo ${{ secrets.DATABASE_URL }}\n"

    assert problems("catalogue-weekly.yml", text) == []


def test_a_workflow_without_top_level_permissions_is_flagged():
    text = CLEAN.replace("permissions:\n  contents: read\n", "")

    assert any("permissions" in problem for problem in problems("ci.yml", text))


def test_the_workflows_in_this_repo_pass_the_guard():
    root = Path(__file__).resolve().parent.parent

    assert check_directory(root / ".github" / "workflows") == []
