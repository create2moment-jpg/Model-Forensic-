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


def test_runs_reading_ranking_noise_tie_nan_and_running() -> None:
    from model_forensic.analysis.chart_insights import explain_runs

    t = np.arange(200)
    rng = np.random.default_rng(1)

    def curve(floor: float, tau: float = 30) -> np.ndarray:
        return 2.0 * np.exp(-t / tau) + floor + rng.normal(0, 0.005, t.size)

    nan_run = curve(0.5)
    nan_run[150:] = math.nan
    runs = {
        "a": ser("train/loss", curve(0.500)),
        "b": ser("train/loss", curve(0.501)),  # tie with a (within noise)
        "c": ser("train/loss", curve(0.700)),
        "live": ser("train/loss", curve(0.1)),  # would be "best" but is still running
        "bad": ser("train/loss", nan_run),
    }
    r = explain_runs("train/loss", runs, registry=default_registry, running={"live"})
    order = [x["run"] for x in r.ranking]
    assert "live" not in order and "bad" not in order and order[-1] == "c"
    assert any("Still running, not ranked: live" in f for f in r.facts)
    assert any("not meaningful" in f for f in r.facts)
    assert any("bad (step" in w for w in r.warnings)
    assert any("seed replicates" in c for c in r.checks)


def test_api_explain_runs(container) -> None:
    c = TestClient(create_app(container))
    runs = ["local:baseline-cl4f-s0", "local:baseline-cl4f-s1", "local:lr-sweep-1e3"]
    r = c.post("/api/v1/metrics/explain-runs", json={"key": "train/loss", "runs": runs}).json()
    assert r["runs"] == 3 and any("lr-sweep-1e3" in w for w in r["warnings"])
    assert c.post("/api/v1/metrics/explain-runs", json={"key": "train/loss", "runs": runs[:1]}).status_code == 400
    a = c.post("/api/v1/metrics/ask-runs", json={"key": "train/loss", "runs": runs, "question": "winner?"}).json()
    assert "No LLM" in a["summary"] and a["claims"]
