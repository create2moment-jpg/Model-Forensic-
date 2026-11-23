"""Stages are never mixed: the analyses see one stage at a time (default: each metric from its last stage)."""

from __future__ import annotations

import numpy as np

from model_forensic.analysis.internals import attention_table
from model_forensic.analysis.loss_questions import ask
from model_forensic.application.stages import merge_stage_series, stage_view
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 40


def _s(key: str, v: np.ndarray) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(float(i) for i in range(N)), values=tuple(map(float, v)))


def _pipeline() -> list[MetricSeries]:
    """pretrain (loss diverges, radar ignored) → finetune (healthy) → evaluation."""
    up = np.concatenate([np.linspace(2, 1, N // 2), np.linspace(1, 3, N // 2)])
    down = np.linspace(1.5, 0.5, N)
    pre = [_s("train/loss", up), _s("train/attn_mass[layer=0,modality=radar]", np.full(N, 0.01)),
           _s("train/attn_mass[layer=0,modality=camera]", np.full(N, 0.99))]  # fmt: skip
    fine = [_s("train/loss", down), _s("train/attn_mass[layer=0,modality=radar]", np.full(N, 0.3)),
            _s("train/attn_mass[layer=0,modality=camera]", np.full(N, 0.7))]  # fmt: skip
    ev = [_s("val/NDS", np.linspace(0.3, 0.5, N))]
    return merge_stage_series([], [("train_pretrain", pre), ("train_finetune", fine), ("evaluate_finetune", ev)])


def test_views_hold_one_stage_each() -> None:
    merged = _pipeline()
    assert {str(s.key) for s in merged} >= {
        "train/loss",
        "train/loss[stage=train_pretrain]",
        "train/loss[stage=train_finetune]",
    }
    default = {str(s.key): s for s in stage_view(merged)}
    assert not any("stage=" in k for k in default)  # no second copy of a metric from another stage
    assert default["train/loss"].values[-1] == 0.5 and "val/NDS" in default  # the last stage that logged each metric
    pre = {str(s.key): s for s in stage_view(merged, "train_pretrain")}
    assert set(pre) == {
        "train/loss",
        "train/attn_mass[layer=0,modality=radar]",
        "train/attn_mass[layer=0,modality=camera]",
    }
    assert pre["train/loss"].values[-1] == 3.0
    assert set(stage_view(merged, "evaluate_finetune")[0].key.dim_dict) == set()


def test_analyses_answer_per_stage_without_mixing() -> None:
    merged = _pipeline()
    fine = {a.id: a for a in ask({str(s.key): s for s in stage_view(merged)})}
    pre = {a.id: a for a in ask({str(s.key): s for s in stage_view(merged, "train_pretrain")})}
    assert fine["diverge"].status == "ok" and pre["diverge"].status == "bad"
    assert pre["attention"].status == "bad" and fine["attention"].status == "ok"
    # attention table on the merged series (before the fix one stage overwrote the other): only the default view is used
    t = attention_table({str(s.key): s for s in stage_view(merged)})
    assert abs(t["mass"]["0"]["radar"]["end"] - 0.3) < 1e-6


def test_api_stage_parameter(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    r = c.get("/api/v1/runs/local:xf-pipe-finetune/loss-questions", params={"stage": "train_finetune"})
    assert r.status_code == 200 and r.json()["stage"] == "train_finetune"
    assert c.get("/api/v1/runs/local:xf-pipe-finetune/loss-questions", params={"stage": "nope"}).status_code == 400
    ev = c.get("/api/v1/runs/local:xf-pipe-finetune/loss-questions", params={"stage": "evaluate_finetune"}).json()
    assert {q["id"]: q for q in ev["questions"]}["diverge"][
        "status"
    ] == "na"  # an evaluation stage has no training loss
