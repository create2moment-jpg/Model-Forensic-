"""Relative paths: your data from PROJECT_PATH, Model Forensic's own files from its folder — not from the cwd."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_forensic.cli.main import app
from model_forensic.knowledge.sources import KnowledgeSource
from model_forensic.settings import Settings
from model_forensic.shared import paths

REPO = Path(__file__).resolve().parents[3]


def test_project_and_home_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PROJECT_PATH", raising=False)
    monkeypatch.delenv("MF_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.project_root() == tmp_path and paths.describe()["project_root_from"] == "current directory"
    assert paths.mf_home() == REPO  # found from the package location, not the cwd
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("PROJECT_PATH", str(proj))
    assert paths.project_path("outputs/_azure_artefacts") == proj / "outputs/_azure_artefacts"
    assert paths.project_path("/abs/x") == Path("/abs/x")
    assert paths.project_path("~/x") == Path.home() / "x"
    monkeypatch.setenv("SOME_DATA", "/data")
    assert paths.project_path("$SOME_DATA/runs") == Path("/data/runs")
    monkeypatch.setenv("MF_HOME", str(tmp_path / "mf"))
    assert paths.home_path("config/azure.local.yaml") == tmp_path / "mf/config/azure.local.yaml"
    assert paths.describe()["project_root_from"] == "$PROJECT_PATH" and paths.describe()["mf_home_from"] == "$MF_HOME"


def test_settings_load_anchors_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home, proj, elsewhere = tmp_path / "mf", tmp_path / "proj", tmp_path / "elsewhere"
    for d in (home, proj, elsewhere):
        d.mkdir()
    (home / "model-forensic.yaml").write_text(
        "data_dir: .mf\nbackends:\n  - {kind: local, name: runs, options: {root: outputs/runs}}\n"
    )
    monkeypatch.setenv("MF_HOME", str(home))
    monkeypatch.setenv("PROJECT_PATH", str(proj))
    monkeypatch.setenv("MF_AZURE_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.delenv("MF_DATA_DIR", raising=False)
    monkeypatch.delenv("MF_KNOWLEDGE_DIR", raising=False)
    monkeypatch.delenv("MF_CONFIG", raising=False)
    monkeypatch.chdir(elsewhere)  # started from anywhere
    s = Settings.load()
    assert s.data_dir == home / ".mf"
    assert s.knowledge_dir == home / "config/knowledge"
    assert s.plugin_dirs == [home / "config/plugins"]
    local = next(b for b in s.backends if b.name == "runs")
    assert local.options["root"] == str(proj / "outputs/runs")


def test_knowledge_sources_resolve_from_the_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROJECT_PATH", str(tmp_path / "proj"))
    monkeypatch.setenv("MF_HOME", str(tmp_path / "mf"))
    assert KnowledgeSource(name="perc", path="docs/perception").resolved() == tmp_path / "proj/docs/perception"
    assert (
        KnowledgeSource(name="inbox", path="config/knowledge", inbox=True).resolved()
        == tmp_path / "mf/config/knowledge"
    )


def test_inspect_resolves_relative_folders_from_project_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    art = proj / "outputs" / "_azure_artefacts"
    art.mkdir(parents=True)
    (art / "metrics.csv").write_text("step,train/loss\n0,1.0\n")
    monkeypatch.setenv("PROJECT_PATH", str(proj))
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["inspect", "outputs/_azure_artefacts", "--json"])
    assert r.exit_code in (0, 1), r.output  # the report itself (0 = ok, 1 = drift notes)
    assert json.loads(r.output)["files"] >= 1
    bad = CliRunner().invoke(app, ["inspect", "outputs/nope"])
    assert bad.exit_code == 2 and str(proj / "outputs/nope") in bad.output and "PROJECT_PATH" in bad.output
    p = CliRunner().invoke(app, ["paths", "--json"])
    assert json.loads(p.output)["project_root"] == str(proj)


def test_setup_script_writes_one_block_to_both_shells(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path)}
    (tmp_path / ".bashrc").write_text("alias ll='ls -l'\n")
    script = REPO / "scripts" / "set-project-path.sh"
    for target in (tmp_path, tmp_path / "proj"):
        target.mkdir(exist_ok=True)
        subprocess.run([str(script), str(target)], check=True, env=env, capture_output=True)
    for rc in (".bashrc", ".zshrc"):
        text = (tmp_path / rc).read_text()
        assert text.count("export PROJECT_PATH=") == 1 and str(tmp_path / "proj") in text
    assert "alias ll='ls -l'" in (tmp_path / ".bashrc").read_text()  # the rest of the file is kept
    r = subprocess.run([str(script), str(tmp_path / "missing")], env=env, capture_output=True)
    assert r.returncode == 2
