"""Expected vs actual: expectations files from the codebase, checks against the run, harness task, API and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml

from model_forensic.application import expectations as ex
from model_forensic.application.harness import brief, harness_dir
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 100
X = np.linspace(0, 1, N)


def _s(key: str, v: np.ndarray) -> MetricSeries:
    return MetricSeries(
        key=MetricKey.parse(key), steps=tuple(float(i * 10) for i in range(N)), values=tuple(map(float, v))
    )


def _series(weighted: bool = True, vel_stops: bool = False) -> dict[str, MetricSeries]:
    cls = 1.1 * np.exp(-3 * X) + 0.3
    bbox = 1.4 * np.exp(-2 * X) + 0.2
    vel = (
        np.where(X < 0.25, 0.8 * np.exp(-4 * X) + 0.3, 0.8 * np.exp(-1) + 0.3)
        if vel_stops
        else 0.9 * np.exp(-2 * X) + 0.2
    )
    tot = 2.0 * cls + 0.25 * bbox + 0.25 * vel if weighted else cls + bbox + vel
    s = {
        k: _s(k, v)
        for k, v in (("train/loss/cls", cls), ("train/loss/bbox", bbox), ("train/loss/vel", vel), ("train/loss", tot))
    }
    for k in range(4):
        s[f"train/loss/cls[layer={k}]"] = _s(f"train/loss/cls[layer={k}]", cls * (1 + 0.1 * (3 - k)))
    return s


EXP = ex.Expectations.model_validate(
    {
        "runs": ["local:r1"],
        "terms": [
            {
                "name": "train/loss/cls",
                "weight": 2.0,
                "weight_key": "loss.cls_weight",
                "initial": {"value": 1.4},
                "range": {"min": 0},
            },
            {"name": "train/loss/cls[layer=*]", "in_total": False, "layers": 4, "layer_order": "decreasing"},
            {"name": "train/loss/bbox", "weight": 0.25, "initial": {"value": 1.6}},
            {"name": "train/loss/vel", "weight": 0.25},
            {"name": "train/loss/dn", "required": True},
        ],
        "total": {"name": "train/loss", "composition": "weighted_sum"},
        "config": [{"key": "model.num_classes", "equals": 10}],
    }
)


def _by(checks: list[dict]) -> dict[str, dict]:  # type: ignore[type-arg]
    return {c["id"]: c for c in checks}


def test_healthy_run_matches_the_code() -> None:
    c = _by(ex.check(EXP, _series(), {"loss.cls_weight": 2.0, "model.num_classes": 10}))
    assert c["composition"]["status"] == "ok"
    assert c["initial:train/loss/cls"]["status"] == "ok" and c["initial:train/loss/bbox"]["status"] == "ok"
    assert "order:train/loss/cls[layer=*]" not in c and c["config:model.num_classes"]["status"] == "ok"
    assert c["presence:train/loss/dn"]["status"] == "bad"  # required but not logged


def test_mismatches_point_at_their_cause() -> None:
    c = _by(ex.check(EXP, _series(weighted=False, vel_stops=True), {"loss.cls_weight": 1.0, "model.num_classes": 3}))
    comp = c["composition"]
    assert comp["status"] == "bad" and comp["hypothesis"] == "implementation" and "cls 1" in comp["actual"]
    assert c["direction:train/loss/vel"]["hypothesis"] == "data"  # stops learning early: missing targets
    assert c["weightkey:train/loss/cls"]["status"] == "bad" and c["weightkey:train/loss/cls"]["hypothesis"] == "config"
    assert c["config:model.num_classes"]["status"] == "bad"
    low = ex.Expectations.model_validate({"terms": [{"name": "train/loss/cls", "initial": {"value": 5.0}}]})
    assert _by(ex.check(low, _series(), {}))["initial:train/loss/cls"]["hypothesis"] == "data"


def test_matching_files_schema_and_harness_task(container, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert ex.matches(EXP, {"local:r1"}, {}) and not ex.matches(EXP, {"local:r2"}, {})
    by_cfg = ex.Expectations(when_config={"loss.*_weight": "2*"})
    assert ex.matches(by_cfg, set(), {"loss.cls_weight": 2.0}) and not ex.matches(by_cfg, set(), {"loss.cls_weight": 1})
    d = ex.folder(tmp_path)
    d.mkdir(parents=True)
    (d / "ok.yaml").write_text(yaml.safe_dump(EXP.model_dump()))
    (d / "bad.yaml").write_text("terms: 3\n")
    files, errors = ex.load_all(tmp_path)
    assert len(files) == 1 and errors and errors[0].startswith("bad.yaml")
    current = (harness_dir() / "expectations.schema.json").read_text()
    assert json.loads(current) == json.loads(ex.schema_text()), "run: mf harness schema"
    text = brief(container.service, container.knowledge, "local:cand-radar-8f", task="loss_expectations")
    assert "# Task: loss expectations" in text and "expectations/" in text
    assert "Output contract (mandatory)" in text and "status: proposed" not in text  # its own contract, not knowledge


def test_demo_api_and_cli(container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli

    c = TestClient(create_app(container))
    assert c.get("/api/v1/runs/local:cand-radar-8f/expectations").json()["found"] is False
    cli._demo_expectations(container.knowledge.folder)
    d = c.get("/api/v1/runs/local:cand-radar-8f/expectations").json()
    assert d["found"] and d["counts"]["bad"] >= 1
    assert any(ch["id"] == "composition" and ch["status"] == "bad" for ch in d["checks"])  # demo total is unweighted
    monkeypatch.setattr(cli, "_c", lambda _cfg=None: container)
    r = CliRunner()
    res = r.invoke(cli.app, ["expectations", "--run", "local:cand-radar-8f"])
    assert res.exit_code == 0 and "weighted sum" in res.output
    f = ex.folder(container.knowledge.folder) / "demo-detector.yaml"
    assert r.invoke(cli.app, ["expectations", "--check", str(f)]).exit_code == 0
