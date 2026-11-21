"""Expert features: notebook bridge, local plugins, launch gate, noise floor / effect sizes."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from model_forensic.analysis.local_plugins import install
from model_forensic.api.app import create_app
from model_forensic.application.gate import gate
from model_forensic.application.noise import effects, noise_floor
from model_forensic.notebook import Forensic

pd = pytest.importorskip("pandas")
EXAMPLE = Path(__file__).parents[3] / "config" / "plugins" / "example_analysis.py.example"


def test_notebook_bridge(container) -> None:
    mf = Forensic(container)
    runs = mf.runs("experiment:sparse_detection")
    assert {"run", "status", "experiment"} <= set(runs.columns) and len(runs) >= 4
    long = mf.series("local:xf-pipe-finetune", "train/uloss/*")
    assert len(long) and long["raw_name"].str.contains("unweighted_loss").all()
    assert mf.wide("local:xf-pipe-finetune", "train/loss").shape[1] == 1
    assert mf.lr("local:xf-pipe-finetune") is not None
    assert {"P1", "P5"} <= set(mf.checks("local:xf-pipe-finetune")["id"])
    pc = mf.per_class("local:xf-pipe-finetune")
    assert set(pc["stage"]) == {"evaluate_pretrain", "evaluate_finetune"} and "AP" in set(pc["metric"])
    seq = mf.sequences("local:xf-pipe-finetune")
    assert any(df.index.names == ["sequence_hash", "class_name"] for df in seq.values())
    f = mf.findings("local:xf-pipe-finetune", analyses=["study_checks"])
    assert (f["analysis"] == "study_checks").all()
    assert len(mf.compare("local:baseline-cl4f-s0", "local:cand-radar-8f"))


def test_noise_and_effects(container) -> None:
    seeds = ["local:baseline-cl4f-s0", "local:baseline-cl4f-s1", "local:baseline-cl4f-s2"]
    rows = noise_floor(container.service, seeds)
    nds = next(r for r in rows if r.metric == "val/NDS")
    assert nds.n == 3 and nds.std > 0
    eff = {e.metric: e for e in effects(container.service, seeds, "local:cand-radar-8f")}
    assert eff["val/NDS"].z is not None and eff["val/NDS"].ci95 is not None
    c = TestClient(create_app(container))
    assert c.get("/api/v1/noise", params={"runs": seeds}).status_code == 200
    assert c.get("/api/v1/noise", params={"runs": seeds[:1]}).status_code == 400


def test_local_plugins(container, tmp_path: Path) -> None:
    shutil.copy(EXAMPLE, tmp_path / "team.py")
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')")
    (tmp_path / "dup.py").write_text(
        "from model_forensic.analysis.plugins.study import LOSS_LEDGER\nPLUGINS = (LOSS_LEDGER,)\n"
    )
    reg = container.service.analyses
    names, errors = install(reg, [tmp_path])
    assert names == {"team_loss_limit"}
    assert any("broken.py" in e for e in errors) and any("loss_ledger" in e for e in errors)
    res = container.service.analyze(
        "local:baseline-cl4f-s0", analyses=["team_loss_limit"], params={"team_loss_limit": {"limit": 0.1}}
    )
    assert res[0].findings and res[0].findings[0].title.startswith("Loss above the team limit")
    names2, _ = install(reg, [], previous=names)
    assert not names2 and "team_loss_limit" not in {p.name for p in reg.all()}


def test_gate_rules() -> None:
    no_val = {
        "trainer": {"best_checkpoint_monitor": "val_total_loss_epoch", "limit_val_batches": 0},
        "tasks": {"eval": {"combined_metrics_yaml": "/a/combined_metrics/offline/gt.yml"}},
    }
    st = {r.id: r.status for r in gate(no_val)}
    assert st == {"G1": "PASS", "G2": "FAIL", "G3": "FAIL"}
    ok = {
        "trainer": {"check_val_every_n_epoch": 1, "best_checkpoint_monitor": "val/NDS"},
        "tasks": {"eval": {"combined_metrics_yaml": "/b/combined_metrics/offline/gt.yml"}},
    }
    st = {r.id: r.status for r in gate(ok, logged=["val/NDS"], reference={"x": no_val})}
    assert st == {"G1": "PASS", "G2": "PASS", "G3": "PASS"}
    other = {**ok, "tasks": {"eval": {"combined_metrics_yaml": "/b/combined_metrics/offline/seq.yml"}}}
    assert {r.id: r.status for r in gate(other, reference={"x": no_val})}["G1"] == "FAIL"


def test_gate_cli_and_api(container, tmp_path: Path) -> None:
    from model_forensic.cli.main import app

    f = tmp_path / "train.yaml"
    f.write_text("trainer:\n  best_checkpoint_monitor: val_total_loss_epoch\n")
    r = CliRunner().invoke(app, ["gate", str(f), "--json"])
    assert r.exit_code == 1 and '"G3"' in r.stdout
    c = TestClient(create_app(container))
    out = c.post("/api/v1/gate", json={"config": f.read_text(), "against": "local:xf-pipe-finetune"}).json()
    assert {x["id"] for x in out} == {"G1", "G2", "G3"}
    assert c.post("/api/v1/analyses/reload").json()["errors"] == []
