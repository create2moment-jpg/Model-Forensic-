"""Pipelines in the Metrics tab: every metric as stage / metric, the last stage's plain copy not listed twice."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_forensic.application.stages import merge_stage_series, metric_catalog
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split


def _s(name: str, split: Split, dims: tuple[tuple[str, str], ...] = ()) -> MetricSeries:
    return MetricSeries(key=MetricKey(name=name, split=split, dims=dims), steps=(0.0, 1.0), values=(1.0, 0.5))


def test_two_training_stages_are_listed_per_stage() -> None:
    pre = [_s("loss", Split.TRAIN), _s("lr", Split.TRAIN), _s("loss", Split.TRAIN, (("layer", "3"),))]
    fine = [_s("loss", Split.TRAIN), _s("loss", Split.TRAIN, (("layer", "3"),)), _s("mAP", Split.VAL)]
    merged = merge_stage_series([], [("train_pretrain", pre), ("train_finetune", fine)])
    cat = metric_catalog(merged, ["train_pretrain", "train_finetune"])
    labels = [e["label"] for e in cat["entries"]]
    assert cat["stages"] == ["train_pretrain", "train_finetune"]
    assert "train_pretrain / train/loss" in labels and "train_finetune / train/loss" in labels
    assert "train_pretrain / train/lr" in labels and "train_finetune / val/mAP" in labels
    assert "train_finetune / train/loss[layer=3]" in labels
    assert "train/loss" not in labels  # the plain copy of the last stage is not listed twice
    assert len(labels) == len(set(labels))
    pre_keys = [e for e in cat["entries"] if e["stage"] == "train_pretrain"]
    assert all("stage=" in str(e["key"]) or e["metric"] == "train/lr" for e in pre_keys)
    assert [e["stage"] for e in cat["entries"]] == sorted((e["stage"] for e in cat["entries"]), key=cat["stages"].index)


def test_plain_runs_have_no_stages(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    plain = api.get("/api/v1/runs/local:baseline-cl4f-s0/metric-catalog").json()
    assert plain["stages"] == [] and all(e["stage"] is None and e["label"] == e["key"] for e in plain["entries"])
    pipe = api.get("/api/v1/runs/local:xf-pipe-finetune/metric-catalog").json()
    assert pipe["stages"] and all(e["stage"] in pipe["stages"] for e in pipe["entries"] if e["stage"])
