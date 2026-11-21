"""'Explain this chart': deterministic facts, warnings, markers and checks; the API and the offline answer."""

import math

import numpy as np
from fastapi.testclient import TestClient

from model_forensic.analysis.chart_insights import explain
from model_forensic.analysis.metric_registry import default_registry
from model_forensic.api.app import create_app
from model_forensic.domain.metrics import MetricKey, MetricSeries


def ser(key: str, values) -> MetricSeries:
    return MetricSeries(
        key=MetricKey.parse(key),
        steps=tuple(float(i * 10) for i in range(len(values))),
        values=tuple(float(v) for v in values),
    )


def test_reading_of_a_spiky_loss_with_nan_and_lr_drop() -> None:
    t = np.arange(300)
    loss = 2.0 * np.exp(-t / 40) + 0.5 + 0.002 * np.sin(t)
    loss[120] += 2.0
    loss[250:] = math.nan
    lr = np.where(t < 200, 1e-3, 1e-4)
    r = explain(ser("train/loss", loss), lr=ser("train/lr", lr))
    kinds = {m.kind for m in r.markers}
    assert r.direction == "lower" and r.non_finite == 50
    assert {"nan", "best", "spike", "lr_drop", "plateau"} <= kinds
    assert any("non-finite" in w for w in r.warnings) and any("spike" in w for w in r.warnings)
    assert any("fp32" in c for c in r.checks) and any("gradient norm" in c for c in r.checks)
    assert any("learning rate drops 1 time" in f for f in r.facts)


def test_score_reading_baseline_within_noise() -> None:
    rng = np.random.default_rng(0)
    v = 0.6 + rng.normal(0, 0.01, 200)
    r = explain(ser("val/NDS", v), baseline=ser("val/NDS", [0.6, 0.6]), registry=default_registry)
    assert r.direction == "higher"
    assert any("within the step-to-step noise" in f for f in r.facts)


def test_api_explain_and_offline_ask(container) -> None:
    c = TestClient(create_app(container))
    r = c.get("/api/v1/runs/local:lr-sweep-1e3/metrics/explain", params={"key": "train/loss"}).json()
    assert r["non_finite"] > 0 and any(m["kind"] == "nan" for m in r["markers"]) and r["checks"]
    assert c.get("/api/v1/runs/local:lr-sweep-1e3/metrics/explain", params={"key": "val/nope"}).status_code == 404
    a = c.post("/api/v1/runs/local:lr-sweep-1e3/metrics/ask", json={"key": "train/loss", "question": "healthy?"}).json()
    assert "No LLM" in a["summary"] and a["claims"] and all(x["type"] == "RECOMMENDATION" for x in a["claims"])
    ids = {e["id"] for e in a["evidence"]}
    assert all(set(x["evidence"]) <= ids for x in a["claims"])
