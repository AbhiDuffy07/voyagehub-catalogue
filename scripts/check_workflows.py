"""Guard for this public repo's GitHub Actions workflows. Run by CI on every push and PR.

Rules (see the repo's security settings):
- no `pull_request_target` (it runs with secrets on code from forks);
- a workflow that runs on pull requests (ci.yml always, any other if it has a `pull_request`
  trigger) must not reference secrets;
- every workflow sets `timeout-minutes` (a stuck job must not run for the 6-hour default);
- every `uses:` is pinned to a full 40-character commit SHA (a tag can be moved);
- every workflow sets a top-level `permissions:` block;
- `runs-on` names a fixed runner image, never a floating `*-latest` one.

    python scripts/check_workflows.py [workflow_dir]
"""

import re
import sys
from pathlib import Path

_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}(\s+#.*)?$")
_USES = re.compile(r"^\s*-?\s*uses:\s*(\S.*)$")
_TOP_LEVEL_PERMISSIONS = re.compile(r"^permissions:")
_PULL_REQUEST_TRIGGER = re.compile(r"^\s*pull_request\s*:")
_TIMEOUT = re.compile(r"^\s*timeout-minutes:\s*\d+")
_FLOATING_RUNNER = re.compile(r"^\s*runs-on:\s*\S*-latest")


def problems(name: str, text: str) -> list[str]:
    """Everything wrong with one workflow file. Comment lines are ignored."""
    lines = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#")
    ]
    found: list[str] = []

    if not any(_TOP_LEVEL_PERMISSIONS.match(line) for _, line in lines):
        found.append(f"{name}: add a top-level `permissions:` block (read-only by default)")

    if not any(_TIMEOUT.match(line) for _, line in lines):
        found.append(f"{name}: set `timeout-minutes` on every job")

    runs_on_pull_requests = name == "ci.yml" or any(
        _PULL_REQUEST_TRIGGER.match(line) for _, line in lines
    )
    for number, line in lines:
        if "pull_request_target" in line:
            found.append(f"{name}:{number}: pull_request_target is not allowed in this public repo")
        if runs_on_pull_requests and "secrets." in line:
            found.append(
                f"{name}:{number}: a workflow that runs on pull requests must not use secrets"
            )
        if _FLOATING_RUNNER.match(line):
            found.append(f"{name}:{number}: pin the runner image (for example ubuntu-24.04)")
        uses = _USES.match(line)
        if uses:
            ref = uses.group(1).strip()
            if not ref.startswith("./") and not _PINNED.match(ref):
                found.append(
                    f"{name}:{number}: `uses: {ref}` must be pinned to a full 40-character SHA"
                )
    return found


def check_directory(directory: Path) -> list[str]:
    found: list[str] = []
    if directory.is_dir():
        for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
            found.extend(problems(path.name, path.read_text(encoding="utf-8")))
    return found


def main(argv: list[str]) -> int:
    directory = Path(argv[0]) if argv else Path(".github/workflows")
    found = check_directory(directory)
    for problem in found:
        print(f"workflow guard: {problem}", file=sys.stderr)
    if not found:
        print("workflow guard: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
