"""Many projects through one unchanged core: profiles (config, not code), isolation, naming, stage roles, contract."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from model_forensic.container import Container, build
from model_forensic.domain.metrics import MetricKey, Split
from model_forensic.projects import CONTRACT_VERSION, load_profiles, parse_profile
from model_forensic.settings import BackendConfig, Settings

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "config" / "projects"


def _write_run(root: Path, rid: str, experiment: str, metrics: dict[str, list[float]], **extra: object) -> None:
    d = root / rid
    (d / "outputs").mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(
        json.dumps(
            {
                "name": rid,
                "experiment": experiment,
                "status": "completed",
                "kind": "training",
                "created_at": "2026-11-01T10:00:00+00:00",
                "user": "someone",
                **extra,
            }
        )
    )
    with (d / "metrics.jsonl").open("w") as f:
        for name, vals in metrics.items():
            for i, v in enumerate(vals):
                f.write(json.dumps({"key": name, "step": i * 10, "value": v}) + "\n")
    (d / "config.yaml").write_text("optimizer:\n  lr: 0.001\n")
    (d / "outputs" / "report.txt").write_text("evaluation summary " * 10)
    (d / "logs").mkdir(exist_ok=True)
    (d / "logs" / "std_log.txt").write_text("started\nfinished\n")


@pytest.fixture()
def three_projects(tmp_path: Path, demo_root: Path, monkeypatch: pytest.MonkeyPatch) -> Container:
    proj = tmp_path / "project"
    lanes, roads = proj / "outputs" / "lane_runs", proj / "outputs" / "road_runs"
    for i in range(3):
        _write_run(
            lanes,
            f"lanes-run{i}",
            "lanes-v2",
            {"eval/F1_lane": [0.5, 0.6 + i / 100], "eval/F1_lane_far": [0.3, 0.4], "train/total": [2.0, 1.0, 0.5]},
        )
    _write_run(lanes, "other-run", "scratch", {"train/total": [1.0, 0.9]})  # not a lanes-* experiment
    for i in range(2):
        _write_run(roads, f"road-run{i}", "roads", {"valid/acc": [0.8, 0.85], "train/ce": [1.0, 0.6]})
    pdir = tmp_path / "mf" / "config" / "projects"
    pdir.mkdir(parents=True)
    shutil.copy(EXAMPLES / "lane-detection.example.yaml", pdir / "lane-detection.local.yaml")
    shutil.copy(EXAMPLES / "road-attributes.example.yaml", pdir / "road-attributes.local.yaml")
    sf = (
        (EXAMPLES / "sensor-fusion.example.yaml")
        .read_text()
        .replace("[my-workspace]", "[local]")
        .replace('["sparse_detection*"]', "[]")
    )
    (pdir / "sensor-fusion.local.yaml").write_text(sf)
    monkeypatch.setenv("PROJECT_PATH", str(proj))
    c = build(
        Settings(
            data_dir=tmp_path / ".mf",
            backends=[BackendConfig(kind="local", name="local", options={"root": str(demo_root)})],
            artifact_min_free_gb=0.0,
            knowledge_dir=tmp_path / "knowledge",
            projects_dir=pdir,
        )
    )
    c.service.sync()
    return c


def test_examples_are_valid_profiles_for_the_current_contract() -> None:
    files = sorted(EXAMPLES.glob("*.example.yaml"))
    assert {f.name for f in files} == {
        "sensor-fusion.example.yaml",
        "lane-detection.example.yaml",
        "road-attributes.example.yaml",
    }
    for f in files:
        p, err = parse_profile(f.read_text(), f.name)
        assert err is None and p is not None and p.contract_version == CONTRACT_VERSION


def test_three_projects_run_side_by_side_isolated(three_projects: Container) -> None:
    svc = three_projects.service
    assert {p.id for p in svc.projects} == {"sensor-fusion", "lane-detection", "road-attributes"}
    lanes = svc.search("", limit=500, project="lane-detection").runs
    assert {r.ref.run_id for r in lanes} == {"lanes-run0", "lanes-run1", "lanes-run2"}  # experiment glob applied
    roads = svc.search("", limit=500, project="road-attributes").runs
    assert {r.ref.backend for r in roads} == {"roads"} and len(roads) == 2
    fusion = svc.search("", limit=500, project="sensor-fusion").runs
    assert fusion and {r.ref.backend for r in fusion} == {"local"}
    assert not ({r.ref for r in lanes} & {r.ref for r in fusion})  # no project sees another's runs
    assert {e["name"] for e in svc.experiments(project="lane-detection")} == {"lanes-v2"}
    assert svc.project_of("lanes:lanes-run0").id == "lane-detection"
    assert svc.project_of("lanes:other-run") is None  # outside the project's experiments


def test_project_metric_names_reach_the_core_as_canonical_keys(three_projects: Container) -> None:
    svc = three_projects.service
    keys = {s.key for s in svc.series("lanes:lanes-run1")}
    assert MetricKey(name="f1", split=Split.VAL) in keys
    assert MetricKey.parse("val/f1[range=far]") in keys
    assert MetricKey(name="loss", split=Split.TRAIN) in keys
    raw = {s.provenance.get("raw_name") for s in svc.series("lanes:lanes-run1")}
    assert "eval/F1_lane" in raw or None in raw  # the project's own name stays as provenance where recorded
    road = {str(s.key) for s in svc.series("roads:road-run0")}
    assert {"val/accuracy", "train/loss"} <= road
    # the demo project (no aliases) is untouched
    assert MetricKey(name="loss", split=Split.TRAIN) in {s.key for s in svc.series("local:baseline-cl4f-s0")}


def test_stage_roles_terminology_and_contract(three_projects: Container) -> None:
    p = next(x for x in three_projects.service.projects if x.id == "lane-detection")
    assert p.stage_role("fit_main") == "training" and p.stage_role("bench_highway") == "evaluation"
    assert p.stage_role("evaluate_x") == "evaluation"  # default rules still apply
    assert p.term("class") == "lane type" and p.term("run") == "run"
    from model_forensic.projects.contract import check_run_source, summary

    for b in p.backends:
        assert summary(check_run_source(three_projects.service.sources[b]))["ok"]


def test_cli_projects_list_and_check(
    three_projects: Container, tmp_path: Path, demo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from model_forensic.cli.main import app

    home = tmp_path / "mf"  # holds config/projects/*.local.yaml (written by the fixture)
    (home / "model-forensic.yaml").write_text(
        f"data_dir: .mf\nbackends:\n  - {{kind: local, name: local, options: {{root: {demo_root}}}}}\n"
    )
    monkeypatch.setenv("MF_HOME", str(home))
    monkeypatch.setenv("MF_AZURE_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.delenv("MF_CONFIG", raising=False)
    monkeypatch.delenv("MF_DATA_DIR", raising=False)
    r = CliRunner().invoke(app, ["projects", "list"])
    assert r.exit_code == 0 and "lane-detection" in r.output and "road-attributes" in r.output
    r = CliRunner().invoke(app, ["projects", "check", "lane-detection", "--json"])
    rep = json.loads(r.output)
    assert r.exit_code == 0 and rep["ok"] and rep["workspaces"]["lanes"]["ok"]
    assert CliRunner().invoke(app, ["projects", "check", "nope"]).exit_code == 2


def test_profile_problems_are_reported_not_fatal(tmp_path: Path) -> None:
    d = tmp_path / "p"
    d.mkdir()
    (d / "a.local.yaml").write_text("id: alpha\nworkspaces: [ws1]\n")
    (d / "b.local.yaml").write_text("id: beta\nworkspaces: [ws1]\n")  # ws1 already belongs to alpha
    (d / "c.local.yaml").write_text("id: alpha\n")  # duplicate id
    (d / "d.local.yaml").write_text(f"id: delta\ncontract_version: {CONTRACT_VERSION + 1}\n")
    (d / "e.local.yaml").write_text("id: Bad Id\n")
    (d / "f.local.yaml").write_text("id: phi\nmetrics: {rules: [{match: '(', to: x}]}\n")  # invalid regex
    (d / "ignored.example.yaml").write_text("id: example\n")
    profiles, notes = load_profiles(d)
    assert [p.id for p in profiles] == ["alpha"]
    text = " ".join(notes)
    assert "already belong to project 'alpha'" in text and "used twice" in text
    assert "upgrade Model Forensic" in text and "id:" in text and len(notes) == 5


def test_api_projects_and_scoped_search(three_projects: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(three_projects))
    body = api.get("/api/v1/projects").json()
    assert body["contract_version"] == CONTRACT_VERSION
    lane = next(p for p in body["projects"] if p["id"] == "lane-detection")
    assert lane["workspaces"] == ["lanes"] and lane["aliases"] == 4 and lane["unavailable"] == []
    r = api.get("/api/v1/runs", params={"project": "road-attributes"}).json()
    assert r["total"] == 2
    assert api.get("/api/v1/runs", params={"project": "nope"}).status_code == 404
    assert {e["name"] for e in api.get("/api/v1/experiments", params={"project": "lane-detection"}).json()} == {
        "lanes-v2"
    }
