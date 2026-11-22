"""Target-setup protocol: the compatibility report on synthetic targets, and the setup-agent prompt folder."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_forensic.application.setup_check import ALLOWED_PATHS, FORBIDDEN_PATHS, check, record, text
from model_forensic.container import build
from model_forensic.settings import BackendConfig, Settings

REPO = Path(__file__).resolve().parents[3]
AGENT = REPO / "setup-agent"


def _settings(home: Path, backends: list[BackendConfig]) -> Settings:
    return Settings(
        data_dir=home / ".mf",
        backends=backends,
        artifact_min_free_gb=0.0,
        knowledge_dir=home / "config" / "knowledge",
        projects_dir=home / "config" / "projects",
    )


def test_empty_target_reports_what_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    monkeypatch.delenv("PROJECT_PATH", raising=False)
    rep = check(_settings(tmp_path, []))
    by = {r.id: r for r in rep.requirements}
    assert by["C1"].status == "missing" and "mf init" in by["C1"].fix
    assert by["P1"].status == "unknown" and "set-project-path" in by["P1"].fix
    assert [r.id for r in rep.blocking] == ["C1"]
    d = rep.to_dict()
    assert d["ok"] is False and d["counts"]["missing"] >= 1
    assert tuple(d["allowed_paths"]) == ALLOWED_PATHS and "backend/src/" in d["forbidden_paths"]
    assert "Blocking: C1" in text(rep) and "Changes allowed only in" in text(rep)
    assert all(not r.fix for r in rep.requirements if r.status == "satisfied")


def test_configured_target_with_projects_and_live_contract(
    tmp_path: Path, demo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    monkeypatch.setenv("PROJECT_PATH", str(tmp_path))
    pdir = tmp_path / "config" / "projects"
    pdir.mkdir(parents=True)
    (pdir / "demo.local.yaml").write_text("id: demo\nworkspaces: [local]\n")
    (pdir / "ghost.local.yaml").write_text("id: ghost\nworkspaces: [not-configured]\n")
    s = _settings(tmp_path, [BackendConfig(kind="local", name="local", options={"root": str(demo_root)})])
    c = build(s)
    rep = check(c.settings, c.service, live=True)
    by = {r.id: r for r in rep.requirements}
    assert by["C1"].status == "satisfied" and by["P1"].status == "satisfied"
    assert by["J-demo"].status == "satisfied" and by["J-ghost"].status == "missing"
    assert by["K-demo-local"].status == "satisfied"  # adapter contract v1 against the real workspace
    assert {r.id for r in rep.blocking} == {"J-ghost"}
    demo = next(p for p in rep.projects if p["id"] == "demo")
    assert demo["contract"]["local"]["ok"]
    path = record(rep, tmp_path)
    assert path == tmp_path / "config/setup/compatibility.local.json"
    assert json.loads(path.read_text())["model_forensic"] and json.loads(path.read_text())["contract_version"] == 1


def test_privacy_check_catches_a_local_file_that_would_be_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("config/azure.local.yaml\n")  # forgets the others
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    rep = check(_settings(tmp_path, []))
    by = {r.id: r for r in rep.requirements}
    assert by["G-config/azure.local.yaml"].status == "satisfied"
    assert by["G-config/projects/x.local.yaml"].status == "incompatible"
    assert "must not reach the repository" in by["G-config/projects/x.local.yaml"].fix
    # the repository's own .gitignore protects every local file
    real = check.__globals__["_git_ignored"]
    for rel in (
        "config/azure.local.yaml",
        "config/team.local.yaml",
        "config/projects/x.local.yaml",
        "config/setup/x.json",
    ):
        assert real(REPO, rel) in (True, None), rel


def test_cli_setup_check(tmp_path: Path, demo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from model_forensic.cli.main import app

    (tmp_path / "model-forensic.yaml").write_text(
        f"data_dir: .mf\nbackends:\n  - {{kind: local, name: local, options: {{root: {demo_root}}}}}\n"
    )
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    monkeypatch.setenv("PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("MF_AZURE_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.delenv("MF_CONFIG", raising=False)
    monkeypatch.delenv("MF_DATA_DIR", raising=False)
    r = CliRunner().invoke(app, ["setup", "check", "--json", "--live"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["ok"] is True
    assert (tmp_path / "config/setup/compatibility.local.json").is_file()


def test_setup_agent_folder_follows_the_protocol() -> None:
    files = sorted(p.name for p in AGENT.glob("*.md"))
    assert files == [
        "01_target_discovery.md",
        "02_preconditions_and_expectations.md",
        "03_compatibility_check.md",
        "04_interface_adaptation.md",
        "05_validation.md",
        "06_multi_project_setup.md",
        "README.md",
    ]
    readme = (AGENT / "README.md").read_text()
    order = ["DISCOVER", "DECLARE PRECONDITIONS", "CHECK TARGET", "REPORT GAP", "ADAPT INTERFACE", "VALIDATE", "RUN"]
    positions = [readme.index(w) for w in order]
    assert positions == sorted(positions)
    for f in AGENT.glob("*.md"):
        t = f.read_text()
        assert "mf " in t or f.name == "06_multi_project_setup.md"  # every step names a real command
    for p in FORBIDDEN_PATHS[:2]:
        assert p in readme and p in (AGENT / "04_interface_adaptation.md").read_text()
    for p in ("config/projects/<id>.local.yaml", "config/azure.local.yaml"):
        assert p in (AGENT / "04_interface_adaptation.md").read_text()
    assert "interface gap" in (AGENT / "03_compatibility_check.md").read_text()
    assert "Never write to Azure" in readme and "never commit" in readme
