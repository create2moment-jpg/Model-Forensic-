"""Where and why runs diverge: divergence point, outcome, sparse evaluations, LR context, fitting config changes."""

import math

import numpy as np
from fastapi.testclient import TestClient

from model_forensic.analysis.divergence import compare_curves, diverge
from model_forensic.analysis.metric_registry import default_registry
from model_forensic.api.app import create_app
from model_forensic.domain.config import ChangeKind, ConfigChange
from model_forensic.domain.metrics import MetricKey, MetricSeries

T = np.arange(400, dtype=float)
RNG = np.random.default_rng(3)


def ser(key: str, v, steps=None) -> MetricSeries:
    steps = T * 10 if steps is None else steps
    return MetricSeries(
        key=MetricKey.parse(key), steps=tuple(float(x) for x in steps), values=tuple(float(x) for x in v)
    )


def loss(floor: float = 0.5, tau: float = 60) -> np.ndarray:
    return 2.0 * np.exp(-T / tau) + floor + RNG.normal(0, 0.003, T.size)


def test_behind_from_the_lr_drop_with_a_fitting_change() -> None:
    ref = loss()
    cand = ref.copy() + RNG.normal(0, 0.003, T.size)
    cand[200:] += 0.08  # gap opens at step 2000 and stays
    lr_ref = ser("train/lr", np.full(T.size, 1e-3))
    lr_c = ser("train/lr", np.where(T < 200, 1e-3, 1e-4))
    ch = [
        ConfigChange(
            path="scheduler.milestones",
            kind=ChangeKind.CHANGED,
            baseline=[300],
            candidate=[200],
            category="optimization",
        ),
        ConfigChange(path="output_dir", kind=ChangeKind.CHANGED, baseline="a", candidate="b", is_noise=True),
    ]
    d = diverge(
        "cand",
        ser("train/loss", ref),
        ser("train/loss", cand),
        registry=default_registry,
        ref_lr=lr_ref,
        cand_lr=lr_c,
        changes=ch,
    )
    assert d.outcome == "behind" and d.final_gap is not None and d.final_gap < -0.05
    assert d.diverge_step is not None and 1900 <= d.diverge_step <= 2200
    assert "LR drops at step 2000" in d.lr_note
    assert d.fits == ["scheduler.milestones: [300] → [200]"]


def test_same_closed_crossed_and_nan_note() -> None:
    ref = loss()
    same = ref + RNG.normal(0, 0.003, T.size)
    closed = ref.copy()
    closed[100:250] += 0.2
    crossed = ref.copy()
    crossed[50:200] -= 0.15
    crossed[200:] += 0.15
    crossed[380:] = math.nan
    r = compare_curves(
        "train/loss",
        "ref",
        {
            "ref": ser("train/loss", ref),
            "same": ser("train/loss", same),
            "closed": ser("train/loss", closed),
            "crossed": ser("train/loss", crossed),
        },
        registry=default_registry,
    )
    out = {x.run: x for x in r.rows}
    assert out["same"].outcome == "same"
    assert out["closed"].outcome == "closed"
    assert out["crossed"].outcome == "crossed" and "NaN/Inf from step 3800" in out["crossed"].sentence
    assert "2 of 3 run(s) diverge" in r.summary
    assert any("no config difference" in f for f in out["closed"].fits)


def test_sparse_evaluations_compare_point_by_point() -> None:
    steps = np.array([1000, 2000, 3000, 4000, 5000, 6000.0])
    ref = ser("val/NDS", [0.40, 0.48, 0.52, 0.55, 0.56, 0.57], steps)
    cand = ser("val/NDS", [0.40, 0.48, 0.55, 0.58, 0.60, 0.61], steps)
    d = diverge("cand", ref, cand, registry=default_registry)
    assert d.outcome == "ahead" and d.diverge_step == 3000 and "6 shared evaluation(s)" in d.sentence


def test_api_compare_curves_and_ask(container) -> None:
    c = TestClient(create_app(container))
    runs = ["local:baseline-cl4f-s0", "local:cand-radar-8f", "local:lr-sweep-1e3", "local:baseline-cl4f-s1"]
    r = c.post("/api/v1/compare/curves", json={"runs": runs}).json()
    assert r["reference"] == "baseline-cl4f-s0" and len(r["runs"]) == 4
    loss_k = next(k for k in r["keys"] if k["key"] == "train/loss")
    rows = {x["run"]: x for x in loss_k["rows"]}
    assert rows["cand-radar-8f"]["outcome"] == "behind" and rows["baseline-cl4f-s1"]["outcome"] == "same"
    assert any("optimizer.lr" in f for f in rows["lr-sweep-1e3"]["fits"])
    assert len(loss_k["series"]) == 4 and len(loss_k["series"][0]["steps"]) <= 700
    assert c.post("/api/v1/compare/curves", json={"runs": runs[:1]}).status_code == 400
    a = c.post("/api/v1/compare/ask", json={"runs": runs, "question": "why?"}).json()
    assert "No LLM" in a["summary"] and any("re-run the reference with only" in x["text"] for x in a["claims"])


def test_seed_noise_separates_real_from_noise(container) -> None:
    c = TestClient(create_app(container))
    r = c.post(
        "/api/v1/compare/curves",
        json={"runs": ["local:baseline-cl4f-s0", "local:cand-radar-8f", "local:priya-lr3e4-wd05"]},
    ).json()
    assert len(r["reference_seeds"]) == 2  # s1, s2 share the group tag of s0
    loss_k = next(k for k in r["keys"] if k["key"] == "train/loss")
    rows = {x["run"]: x for x in loss_k["rows"]}
    assert rows["cand-radar-8f"]["seed_verdict"] == "beyond seed noise" and rows["cand-radar-8f"]["seed_z"] < -2
    assert rows["priya-lr3e4-wd05"]["seed_verdict"] == "within seed noise" and rows["priya-lr3e4-wd05"]["seed_n"] == 3


def test_save_comparison_into_study_and_book(container) -> None:
    import json

    c = TestClient(create_app(container))
    st = c.post("/api/v1/studies", json={"title": "Radar", "runs": ["local:cand-radar-8f"]}).json()
    runs = ["local:baseline-cl4f-s0", "local:cand-radar-8f", "local:lr-sweep-1e3"]
    s = c.post(f"/api/v1/studies/{st['id']}/comparisons", json={"runs": runs, "note": "LR or radar?"}).json()
    cmp = s["comparisons"][0]
    assert cmp["reference"] == "baseline-cl4f-s0" and cmp["note"] == "LR or radar?" and cmp["rows"]
    assert "local:lr-sweep-1e3" in [f"{r['backend']}:{r['run_id']}" for r in s["runs"]]  # runs join the study
    assert s["timeline"][-1]["kind"] == "comparison"
    html_path, summary = container.study_book(st["id"])
    assert "Saved comparisons" in html_path.read_text() and "LR or radar?" in html_path.read_text()
    assert "Saved comparisons — where and why the runs diverge" in json.loads(summary.read_text())["chapters"]
    s = c.delete(f"/api/v1/studies/{st['id']}/comparisons/{cmp['id']}").json()
    assert s["comparisons"] == []
    assert c.post(f"/api/v1/studies/{st['id']}/comparisons", json={"runs": runs[:1]}).status_code == 400
