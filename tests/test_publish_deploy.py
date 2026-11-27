import json
from pathlib import Path

import pytest

from catalogue.errors import PipelineError
from catalogue.publish import deploy
from catalogue.publish.run import run_publish_static
from tests import publishfixture


class Recorder:
    def __init__(self, code=0):
        self.calls = []
        self.code = code

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return type("Done", (), {"returncode": self.code})()


def _site(tmp_path) -> Path:
    return run_publish_static(**publishfixture.build(tmp_path)).site_dir


def test_the_wrangler_version_is_pinned_exactly():
    assert deploy.WRANGLER_VERSION == "4.156.0"
    assert deploy.WRANGLER_VERSION.count(".") == 2 and deploy.WRANGLER_VERSION[0].isdigit()


def test_deploy_runs_the_pinned_wrangler_without_a_shell(tmp_path):
    site = _site(tmp_path)
    run = Recorder()
    deploy.deploy(site, project="voyagehub-catalogue", branch="preview", confirmed=True, runner=run)
    ((command, kwargs),) = run.calls
    assert command[-6:] == [
        "pages",
        "deploy",
        str(site),
        "--project-name=voyagehub-catalogue",
        "--branch=preview",
        "--commit-dirty=true",
    ]
    assert f"wrangler@{deploy.WRANGLER_VERSION}" in command
    assert "shell" not in kwargs or kwargs["shell"] is False
    assert "@latest" not in " ".join(command)


def test_deploy_refuses_without_confirmation(tmp_path):
    run = Recorder()
    with pytest.raises(PipelineError, match="--yes"):
        deploy.deploy(_site(tmp_path), project="p", branch=None, confirmed=False, runner=run)
    assert run.calls == []


def test_deploy_refuses_a_site_that_was_edited_after_the_build(tmp_path):
    site = _site(tmp_path)
    (site / "v1" / "cities" / "jaipur.json").write_text("{}\n", encoding="utf-8")
    run = Recorder()
    with pytest.raises(PipelineError, match="jaipur.json does not match"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=run)
    assert run.calls == []


def test_deploy_refuses_a_site_with_an_extra_file(tmp_path):
    site = _site(tmp_path)
    (site / "v1" / "cities" / "stray.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="stray.json"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_passes_an_absolute_site_path(tmp_path, monkeypatch):
    site = _site(tmp_path)
    monkeypatch.chdir(site.parent)
    run = Recorder()
    deploy.deploy(Path("site"), project="p", branch=None, confirmed=True, runner=run)
    command = run.calls[0][0]
    assert Path(command[command.index("deploy") + 1]).is_absolute()


def test_deploy_refuses_a_path_cmd_would_interpret(tmp_path):
    odd = tmp_path / "a&b"
    odd.mkdir()
    with pytest.raises(PipelineError, match="site path"):
        deploy.deploy(odd, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_refuses_manifest_names_that_leave_the_folder(tmp_path):
    site = _site(tmp_path)
    path = site / "v1" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"]["../../evil.json"] = {"sha256": "0" * 64, "bytes": 1}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="not an allowed file name"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_refuses_a_changed_version(tmp_path):
    site = _site(tmp_path)
    path = site / "v1" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="version"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_refuses_a_changed_headers_file(tmp_path):
    site = _site(tmp_path)
    (site / "_headers").write_text("/*\n  X: y\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="_headers"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_refuses_a_missing_manifest(tmp_path):
    with pytest.raises(PipelineError, match="manifest.json"):
        deploy.deploy(tmp_path, project="p", branch=None, confirmed=True, runner=Recorder())


def test_a_failed_wrangler_is_a_pipeline_error_without_output(tmp_path):
    with pytest.raises(PipelineError, match="wrangler failed"):
        deploy.deploy(
            _site(tmp_path), project="p", branch=None, confirmed=True, runner=Recorder(code=1)
        )


def test_project_names_are_checked():
    for bad in ("", "Has Space", "-x", "a;b", "x" * 59):
        with pytest.raises(PipelineError, match="project name"):
            deploy.deploy(Path("."), project=bad, branch=None, confirmed=True, runner=Recorder())


def test_committed_schemas_match_the_models():
    from catalogue.publish.models import SCHEMAS

    root = Path(__file__).resolve().parent.parent / "schemas"
    for name, model in SCHEMAS.items():
        committed = json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert committed == model.model_json_schema(), name


def test_deploy_accepts_the_landing_page_and_refuses_a_changed_one(tmp_path):
    site = _site(tmp_path)
    assert (site / "index.html").is_file()
    runner = Recorder()
    deploy.deploy(site, project="p", branch=None, confirmed=True, runner=runner)
    assert len(runner.calls) == 1

    (site / "index.html").write_text("<p>changed</p>", encoding="utf-8")
    with pytest.raises(PipelineError, match="index.html"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_accepts_the_404_page_and_refuses_a_changed_one(tmp_path):
    site = _site(tmp_path)
    assert (site / "404.html").is_file()
    deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())

    (site / "404.html").write_text("<p>changed</p>", encoding="utf-8")
    with pytest.raises(PipelineError, match="404.html"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())


def test_deploy_still_refuses_any_other_file_at_the_site_root(tmp_path):
    site = _site(tmp_path)
    (site / "other.html").write_text("x", encoding="utf-8")
    with pytest.raises(PipelineError, match="other.html"):
        deploy.deploy(site, project="p", branch=None, confirmed=True, runner=Recorder())
