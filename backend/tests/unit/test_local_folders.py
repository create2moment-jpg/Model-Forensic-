"""Local training / evaluation folders analysed in place: discovery, Lightning metrics.csv and the kit's
metrics.jsonl, configs, logs, status — read-only (nothing is written into the folders)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.application.ports import ListRunsQuery
from model_forensic.infrastructure.connectors.folder.connector import FolderRunSource, discover, preview


def make_runs(root: Path) -> dict[str, Path]:
    a = root / "exp" / "lightning-run"
    v = a / "lightning_logs" / "version_1"
    v.mkdir(parents=True)
    (a / "lightning_logs" / "version_0").mkdir()
    (a / "lightning_logs" / "version_0" / "metrics.csv").write_text("epoch,step,train/loss\n0,0,9.0\n")
    (v / "metrics.csv").write_text(
        "epoch,step,train/loss,val/loss,val/mAP\n0,0,2.0,,\n0,10,1.5,,\n0,10,,1.8,0.31\n1,20,1.1,,\n1,20,,1.4,0.42\n"
    )
    (v / "hparams.yaml").write_text("lr: 0.0002\nbatch_size: 8\n")
    (a / "train.log").write_text("epoch 0 ok\nepoch 1 ok\n")
    b = root / "exp" / "kit-eval"
    (b / "outputs" / "forensic").mkdir(parents=True)
    (b / "outputs" / "forensic" / "metrics.jsonl").write_text(
        "\n".join(
            json.dumps({"key": k, "step": s, "value": v, "ts": 1.0})
            for k, s, v in [("val/NDS", 0, 0.5), ("val/NDS", 1, 0.55), ("val/AP[class=car]", 1, 0.6)]
        )
        + "\n{broken"
    )
    (b / "config.yaml").write_text("model: {backbone: r50}\n")
    old = time.time() - 3600
    for f in [*a.rglob("*"), *b.rglob("*")]:
        os.utime(f, (old, old))
    return {"lightning": a, "kit": b}


def snapshot(root: Path) -> dict[str, tuple[int, float]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime) for p in root.rglob("*")}


def test_discovery_reading_and_status(tmp_path: Path) -> None:
    runs = make_runs(tmp_path)
    before = snapshot(tmp_path)
    assert {d.name for d in discover(tmp_path / "exp")} == {"lightning-run", "kit-eval"}  # a folder of runs
    assert discover(runs["kit"]) == [runs["kit"]]  # one run
    pv = preview(tmp_path / "exp")
    assert pv["ok"] and len(pv["runs"]) == 2  # type: ignore[arg-type]
    assert not preview(tmp_path / "nope")["ok"]
    src = FolderRunSource("local-runs", [tmp_path / "exp"])
    page = src.list_runs(ListRunsQuery(page_size=50))
    by = {r.name: r for r in page.items}
    lr = by["lightning-run"]
    assert lr.status.value == "completed" and lr.kind.value == "training" and lr.experiment == "exp"
    series = {str(s.key): s for s in src.get_metric_series(lr.ref.run_id)}
    assert series["train/loss"].values == (2.0, 1.5, 1.1)  # the newest version_*, blanks skipped
    assert series["val/mAP"].values == (0.31, 0.42) and series["val/loss"].steps == (10.0, 20.0)
    cfg = src.get_configs(lr.ref.run_id)
    assert any(c.source.endswith("hparams.yaml") and c.data["lr"] == 0.0002 for c in cfg)
    assert "epoch 1 ok" in src.read_log(lr.ref.run_id)
    kit = by["kit-eval"]
    ks = {str(s.key): s for s in src.get_metric_series(kit.ref.run_id)}
    assert ks["val/NDS"].values == (0.5, 0.55) and kit.kind.value == "evaluation"  # a broken last line is skipped
    assert any(a.path == "outputs/forensic/metrics.jsonl" for a in src.list_artifacts(kit.ref.run_id))
    assert b"0.55" in b"".join(src.open_artifact(kit.ref.run_id, "outputs/forensic/metrics.jsonl"))
    # a run still writing is running; a traceback at the end of its log is failed
    (runs["lightning"] / "train.log").write_text("Traceback (most recent call last):\n  boom\n")
    assert src.get_run(lr.ref.run_id).status.value == "failed"
    (runs["kit"] / "outputs" / "forensic" / "metrics.jsonl").touch()
    assert src.get_run(kit.ref.run_id).status.value == "running"
    after = snapshot(tmp_path)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {
        str(runs["lightning"] / "train.log"),
        str(runs["kit"] / "outputs/forensic/metrics.jsonl"),
    }  # only the test's own writes


def test_settings_api_adds_folders_and_runs_open(container, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MF_LOCAL_RUNS", str(tmp_path / "local-runs.yaml"))
    make_runs(tmp_path)
    c = TestClient(create_app(container))
    assert c.get("/api/v1/settings/local-runs").json()["folders"] == []
    pv = c.post("/api/v1/settings/local-runs/preview", json={"path": str(tmp_path / "exp")}).json()
    assert len(pv["runs"]) == 2
    assert c.post("/api/v1/settings/local-runs/add", json={"path": str(tmp_path / "nope")}).status_code == 422
    out = c.post("/api/v1/settings/local-runs/add", json={"path": str(tmp_path / "exp")}).json()
    assert out["folders"] == [str((tmp_path / "exp").resolve())] and len(out["runs"]) == 2
    ref = next(r["ref"] for r in out["runs"] if r["name"] == "lightning-run")
    assert ref.startswith("local-runs:")
    cat = c.get(f"/api/v1/runs/{ref}/metric-catalog").json()
    assert {e["metric"] for e in cat["entries"]} >= {"train/loss", "val/mAP"}
    listed = c.get("/api/v1/runs", params={"backend": "local-runs"}).json()["runs"]
    assert {r["name"] for r in listed} == {"lightning-run", "kit-eval"}  # indexed: in the run list
    assert c.put("/api/v1/settings/local-runs", json={"folders": []}).json()["runs"] == []


def test_a_run_folder_whose_results_sit_in_logger_folders(tmp_path: Path) -> None:
    run = tmp_path / "my-train"
    (run / "lightning_logs" / "version_0").mkdir(parents=True)
    (run / "lightning_logs" / "version_0" / "metrics.csv").write_text("step,train/loss\n0,1.0\n")
    (run / "checkpoints").mkdir()
    (run / "checkpoints" / "last.log").write_text("x")
    assert discover(run) == [run]  # not lightning_logs / checkpoints as runs of their own
    one = tmp_path / "solo"
    (one / "results").mkdir(parents=True)
    (one / "results" / "metrics.jsonl").write_text("")
    assert discover(one) == [one]  # a single result subfolder: the folder is the run
