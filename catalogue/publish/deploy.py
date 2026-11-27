"""`deploy` (B13): upload a built site to Cloudflare Pages with a pinned wrangler.

Locally, sign in once with `npx wrangler@<version> login` (browser). In GitHub Actions (B14)
wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from the environment. No token is
read, written or printed by this code. The deploy refuses to run unless the site
folder is exactly what `publish-static` built (every file matches `v1/manifest.json`, none extra)
and the owner passed --yes. Error messages name files only (the logs are public).
"""

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from catalogue.errors import PipelineError
from catalogue.publish.models import ManifestFile
from catalogue.publish.site import FILE_NAME, FIXED_FILES, version_of

# Exact pin: the newest wrangler release that was at least 14 days old on 2026-11-15.
WRANGLER_VERSION = "4.156.0"
PROJECT_NAME_DEFAULT = "voyagehub-catalogue"
PROJECT_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,56}[a-z0-9])?$")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
CMD_METACHARACTERS = set('&|<>^%"!')  # npx is npx.cmd on Windows, which cmd.exe parses


def verify_site(site: Path) -> None:
    """The site folder must be exactly the files the build recorded in its manifest."""
    manifest_path = site / "v1" / "manifest.json"
    try:
        manifest = ManifestFile.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, ValidationError):
        raise PipelineError(
            "v1/manifest.json is missing or damaged; run publish-static first"
        ) from None
    recorded = manifest.files
    for name, info in recorded.items():
        if not FILE_NAME.fullmatch(name):
            raise PipelineError(f"{name} is not an allowed file name")
        path = site / "v1" / name
        if path.is_symlink():
            raise PipelineError(f"{name} is a symbolic link")
        try:
            data = path.read_bytes()
        except OSError:
            raise PipelineError(f"{name} is missing from the site folder") from None
        if hashlib.sha256(data).hexdigest() != info.sha256 or len(data) != info.bytes:
            raise PipelineError(f"{name} does not match the manifest; build the site again")
    if version_of({n: i.sha256 for n, i in recorded.items()}) != manifest.version:
        raise PipelineError("the manifest version does not match its files; build the site again")
    for path in (site / "v1").rglob("*"):
        name = path.relative_to(site / "v1").as_posix()
        if path.is_symlink():
            raise PipelineError(f"{name} is a symbolic link")
        if path.is_file() and name not in recorded and name != "manifest.json":
            raise PipelineError(f"{name} is in the site folder but not in the manifest")
    for path in site.iterdir():
        if path.name not in ("v1", *FIXED_FILES):
            raise PipelineError(f"{path.name} is in the site folder but is not part of the build")
    for name, content in FIXED_FILES.items():
        path = site / name
        if path.exists() and path.read_bytes() != content.encode("utf-8"):
            raise PipelineError(f"{name} does not match the build; build the site again")


def deploy(
    site: Path,
    *,
    project: str,
    branch: str | None,
    confirmed: bool,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    if not PROJECT_NAME.fullmatch(project):
        raise PipelineError("the project name must be lowercase letters, digits and hyphens")
    if branch is not None and not BRANCH_NAME.fullmatch(branch):
        raise PipelineError("the branch name has characters that are not allowed")
    if not confirmed:
        raise PipelineError("deploying publishes the site; run again with --yes to confirm")
    site = site.resolve()
    if CMD_METACHARACTERS & set(str(site)):
        raise PipelineError("the site path has characters a command shell would interpret")
    verify_site(site)
    command = [
        shutil.which("npx") or "npx",
        "--yes",
        f"wrangler@{WRANGLER_VERSION}",
        "pages",
        "deploy",
        str(site),
        f"--project-name={project}",
        *([f"--branch={branch}"] if branch else []),
        "--commit-dirty=true",
    ]
    done = runner(command, check=False)
    if done.returncode != 0:
        raise PipelineError("wrangler failed; its own output is above")
