"""Same metric, other name: suggestions between two runs, aliases for every workspace, and local runs re-read when
their files change (live)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.application.metric_matching import normalise, suggest


def test_normalise_and_suggest() -> None:
    assert normalise("train_loss_step") == normalise("train/loss") == "train/loss"
    assert normalise("val_mAP_epoch") == normalise("val/mAP") == "val/map"
    assert normalise("validation-loss") == "val/loss"
    out = suggest(
        ["train/loss", "val/loss", "val/mAP", "val/NDS", "train/lr"],
        {"train_loss_step": "train_loss_step", "val_loss": "val_loss", "val/mAP_epoch": "val/mAP_epoch",
         "train/lr": "train/lr", "weird_metric": "weird_metric"},
    )  # fmt: skip
    pairs = {s["raw"]: s["to"] for s in out["suggestions"]}
    assert pairs == {"train_loss_step": "train/loss", "val_loss": "val/loss", "val/mAP_epoch": "val/mAP"}
    assert out["matched"] == 1 and "weird_metric" in out["only_candidate"] and "val/NDS" in out["only_reference"]
    # ambiguous: two reference names normalise alike → no guess
    amb = suggest(["train/loss", "train_loss"], {"train-loss": "train-loss"})
    assert amb["suggestions"] == []


def lightning_run(root: Path) -> Path:
    run = root / "local-train"
    (run / "lightning_logs" / "version_0").mkdir(parents=True)
    rows = ["epoch,step,train_loss_step,val_loss"] + [f"0,{i * 10},{2.0 - i * 0.05:.3f}," for i in range(20)]
    (run / "lightning_logs" / "version_0" / "metrics.csv").write_text("\n".join([*rows, "0,200,,1.2"]) + "\n")
    old = time.time() - 3600
    for f in run.rglob("*"):
        os.utime(f, (old, old))
    return run


def test_aliases_line_up_a_local_run_with_another_run(container, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MF_LOCAL_RUNS", str(tmp_path / "local-runs.yaml"))
    monkeypatch.setenv("MF_METRIC_ALIASES", str(tmp_path / "aliases.yaml"))
    run = lightning_run(tmp_path)
    c = TestClient(create_app(container))
    ref = c.post("/api/v1/settings/local-runs/add", json={"path": str(run)}).json()["runs"][0]["ref"]
    base = "local:baseline-cl4f-s0"
    m = c.get("/api/v1/compare/metric-names", params={"baseline": base, "candidate": ref}).json()
    pairs = {s["raw"]: s["to"] for s in m["suggestions"]}
    assert pairs.get("train_loss_step") == "train/loss"
    saved = c.put("/api/v1/settings/metric-aliases", json={"add": pairs, "runs": [ref]}).json()
    assert saved["aliases"]["train_loss_step"] == "train/loss"
    keys = {s.key.__str__() for s in container.service.series(ref)}
    assert "train/loss" in keys and "train_loss_step" not in keys  # re-read under the shared name
    m2 = c.get("/api/v1/compare/metric-names", params={"baseline": base, "candidate": ref}).json()
    assert m2["matched"] > m["matched"]
    removed = c.put("/api/v1/settings/metric-aliases", json={"remove": ["train_loss_step"], "runs": [ref]}).json()
    assert "train_loss_step" not in removed["aliases"]


def test_a_local_run_is_reread_when_its_files_change(container, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MF_LOCAL_RUNS", str(tmp_path / "local-runs.yaml"))
    run = lightning_run(tmp_path)
    c = TestClient(create_app(container))
    ref = c.post("/api/v1/settings/local-runs/add", json={"path": str(run)}).json()["runs"][0]["ref"]
    svc = container.service
    svc.local_ttl_s = 0.0  # the run's status: no wait in the test (5 s in use)
    assert svc.get_run(ref).status.value == "completed"
    n0 = len(next(s for s in svc.series(ref) if "loss" in str(s.key) and "val" not in str(s.key)).values)
    with (run / "lightning_logs" / "version_0" / "metrics.csv").open("a") as fh:
        fh.write("1,210,0.900,\n")  # training goes on
    assert svc.get_run(ref).status.value == "running"  # written just now
    n1 = len(next(s for s in svc.series(ref) if "loss" in str(s.key) and "val" not in str(s.key)).values)
    assert n1 == n0 + 1
