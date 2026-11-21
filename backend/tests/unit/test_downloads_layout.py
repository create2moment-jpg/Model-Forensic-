"""Downloads in the Azure pipeline layout: classification, v1 presets, pipeline stages, manifest, metric export."""

import json

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.domain.artifacts import Artifact, ArtifactKind
from model_forensic.domain.run import RunRef
from model_forensic.infrastructure.artifacts.store import DownloadProfile, select
from model_forensic.infrastructure.connectors.local.connector import classify_artifact

R = RunRef(backend="t", run_id="r")


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("user_logs/std_log.txt", ArtifactKind.LOG),
        ("stage_output/summaries/train/composed_config.yaml", ArtifactKind.CONFIG),
        ("stage_output/summaries/train/metrics.csv", ArtifactKind.METRICS),
        ("stage_output/checkpoints/last.ckpt", ArtifactKind.CHECKPOINT),
        ("stage_output/evaluation/offline/predict/x/per_sequence_detection_metrics.parquet", ArtifactKind.EVALUATION),
        ("stage_output/evaluation/offline/predict/car/pr_curves/pr_curves.json", ArtifactKind.EVALUATION),
        ("stage_output/pipeline_info.json", ArtifactKind.EVALUATION),
    ],
)
def test_classify_pipeline_layout(path: str, kind: ArtifactKind) -> None:
    assert classify_artifact(path) == kind


def test_v1_presets() -> None:
    paths = [
        "user_logs/std_log.txt",
        "stage_output/summaries/train/composed_config.yaml",
        "stage_output/summaries/train/metrics.csv",
        "stage_output/evaluation/offline/m.json",
        "stage_output/evaluation/offline/predict/car/counts.parquet",
        "stage_output/visualizations/x.png",
        "stage_output/checkpoints/last.ckpt",
        "stage_output/pipeline_info.json",
    ]
    arts = [Artifact(run=R, path=p, kind=classify_artifact(p), size_bytes=10) for p in paths]

    def sel(p: DownloadProfile) -> set[str]:
        return {a.path.removeprefix("stage_output/") for a in select(arts, p)}

    assert sel(DownloadProfile.LOGS) == {"user_logs/std_log.txt"}
    assert sel(DownloadProfile.CONFIGS) == {"summaries/train/composed_config.yaml"}
    assert sel(DownloadProfile.METRICS_CSV) == {"summaries/train/metrics.csv"}
    assert sel(DownloadProfile.EVAL_JSON) == {"evaluation/offline/m.json", "pipeline_info.json"}
    assert sel(DownloadProfile.EVAL_PARQUET) == {"evaluation/offline/predict/car/counts.parquet"}
    assert "visualizations/x.png" in sel(DownloadProfile.EVAL_OUTPUTS)
    assert sel(DownloadProfile.CHECKPOINT_ONLY) == {"checkpoints/last.ckpt"}


def test_pipeline_download_plan_manifest_and_export(container) -> None:
    svc = container.service
    pipe = "local:xf-pipe-finetune"
    arts = svc.artifacts(pipe)
    assert {a.stage for a in arts} >= {"train_finetune", "evaluate_pretrain", "evaluate_finetune"}
    plans = svc.plans(pipe, ["evaluate_finetune"])
    assert plans["eval_parquet"]["files"] == 2 and plans["checkpoint_only"]["files"] == 0
    files, prog = svc.download(pipe, DownloadProfile.EVAL_PARQUET, stages=["evaluate_finetune"])
    assert len(files) == 2 and not prog.failed
    assert all(f.artifact.run.run_id == "xf-pipe-finetune-eval-ft" for f in files)
    manifest = json.loads((svc.artifact_store.run_root(svc.ref(pipe)) / "download_manifest.json").read_text())
    assert {e["remote"]["stage"] for e in manifest["files"]} == {"evaluate_finetune"} and len(manifest["files"]) == 2
    _, prog2 = svc.download(pipe, DownloadProfile.EVAL_PARQUET, stages=["evaluate_finetune"])
    assert prog2.cached_files == 2  # skip-existing
    j, c = svc.export_metrics(pipe)
    rows = c.read_text().splitlines()
    assert rows[0] == "key,raw_name,stage,step,value"
    assert any("train_sparse_detection/loss_vel/uncertainty_weight_log_sigma" in r for r in rows)
    assert json.loads(j.read_text())["run"] == pipe


def test_api_plans_and_stage_file(container) -> None:
    c = TestClient(create_app(container))
    pl = c.get("/api/v1/runs/local:xf-pipe-finetune/download/plans").json()
    assert pl["stages"] == ["train_finetune", "evaluate_pretrain", "evaluate_finetune"]
    assert pl["profiles"]["configs"]["files"] >= 1
    sel = "local:xf-pipe-finetune-train::stage_output/summaries/train/composed_config.yaml"
    r = c.get("/api/v1/runs/local:xf-pipe-finetune/artifacts/file", params={"path": sel})
    assert r.status_code == 200 and b"sparse_detection_temporal_ground_truth" in r.content
    assert c.post("/api/v1/runs/local:xf-pipe-finetune/metrics/export").json()["csv"].endswith("metrics.csv")
