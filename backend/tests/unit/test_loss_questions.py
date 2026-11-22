"""Training health questions: each question answers bad / warn / ok / na from synthetic series."""

from __future__ import annotations

import math

import numpy as np

from model_forensic.analysis.loss_questions import ask
from model_forensic.domain.metrics import MetricKey, MetricSeries


def _s(key: str, values: list[float] | np.ndarray) -> MetricSeries:
    v = [float(x) for x in values]
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(float(i * 10) for i in range(len(v))), values=tuple(v))


def _by_id(series: dict[str, MetricSeries], base: dict[str, MetricSeries] | None = None) -> dict[str, object]:
    return {a.id: a for a in ask(series, base)}


N = 80
FALL = np.exp(-np.linspace(0, 4, N)) + 0.1


def test_healthy_run_answers_ok_and_missing_data_answers_na() -> None:
    a = _by_id({"train/loss": _s("train/loss", FALL)})
    assert a["diverge"].status == "ok"  # type: ignore[attr-defined]
    assert a["nonfinite"].status == "ok"  # type: ignore[attr-defined]
    assert a["layers_diverge"].status == "na" and "layer" in a["layers_diverge"].answer  # type: ignore[attr-defined]
    assert a["overfit"].status == "na"  # type: ignore[attr-defined]


def test_diverging_loss_and_nan() -> None:
    up = np.concatenate([FALL[:40], FALL[39] * np.linspace(1, 3, 40)])
    a = _by_id({"train/loss": _s("train/loss", up), "train/loss/cls": _s("train/loss/cls", [*FALL[:-1], math.nan])})
    assert a["diverge"].status == "bad" and "train/loss" in a["diverge"].keys  # type: ignore[attr-defined]
    assert a["diverge"].ideas  # type: ignore[attr-defined]
    assert a["nonfinite"].status == "bad"  # type: ignore[attr-defined]


def test_layer_refinement_diverging_and_saturating() -> None:
    s = {"train/loss": _s("train/loss", FALL)}
    for layer, scale in enumerate([1.0, 0.8, 0.7, 0.65, 0.66, 0.75]):  # the last layer is worse than layer 3
        s[f"train/loss/bbox[layer={layer}]"] = _s(f"train/loss/bbox[layer={layer}]", FALL * scale)
    a = _by_id(s)
    assert a["layers_diverge"].status == "bad"  # type: ignore[attr-defined]
    assert "layer 5" in a["layers_diverge"].answer and "layer 3" in a["layers_diverge"].answer  # type: ignore[attr-defined]
    flat = {"train/loss": _s("train/loss", FALL)}
    for layer, scale in enumerate([1.0, 0.7, 0.6, 0.6, 0.6, 0.6]):
        flat[f"train/loss/cls[layer={layer}]"] = _s(f"train/loss/cls[layer={layer}]", FALL * scale)
    b = _by_id(flat)
    assert b["layers_diverge"].status == "ok" and b["layers_saturate"].status == "warn"  # type: ignore[attr-defined]


def test_terms_overfit_weighting_and_baseline() -> None:
    val = np.concatenate([FALL[:40] + 0.2, (FALL[39] + 0.2) * np.linspace(1, 1.4, 40)])
    s = {
        "train/loss": _s("train/loss", FALL),
        "val/loss": _s("val/loss", val),
        "train/loss/cls": _s("train/loss/cls", FALL * 0.1),
        "train/loss/bbox": _s("train/loss/bbox", FALL * 2),
        "train/loss/vel": _s("train/loss/vel", np.full(N, 0.5)),
        "train/task_log_sigma[task=det]": _s("train/task_log_sigma[task=det]", np.linspace(0, 1.2, N)),
    }
    a = _by_id(s)
    assert a["overfit"].status == "bad"  # type: ignore[attr-defined]
    assert a["term_flat"].status == "warn" and "train/loss/vel" in a["term_flat"].answer  # type: ignore[attr-defined]
    assert a["weighting"].status == "warn"  # type: ignore[attr-defined]
    base = {k: _s(k, np.asarray(v.values) * 0.9) for k, v in s.items()}
    b = _by_id(s, base)
    assert b["base_total"].status == "bad"  # type: ignore[attr-defined]
    assert "Worse" in b["base_terms"].answer  # type: ignore[attr-defined]


def test_api_and_cli(container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli
    from model_forensic.cli.main import app

    client = TestClient(create_app(container))
    rid = "local:cand-radar-8f"
    qs = client.get(f"/api/v1/runs/{rid}/loss-questions", params={"baseline": "local:baseline-cl4f-s0"}).json()
    qs = qs["questions"]
    assert {q["id"] for q in qs} >= {"diverge", "layers_diverge", "plateau", "base_total"}
    assert all(q["status"] in ("bad", "warn", "ok", "na") for q in qs)
    monkeypatch.setattr(cli, "_c", lambda _cfg=None: container)
    res = CliRunner().invoke(app, ["questions", "--run", rid])
    assert res.exit_code == 0 and "Is the loss diverging?" in res.output


def test_convergence_verdicts() -> None:
    lr_decay = np.linspace(1e-3, 1e-6, N)
    conv = _by_id({"train/loss": _s("train/loss", FALL), "train/lr": _s("train/lr", lr_decay)})
    assert conv["convergence"].status == "ok" and "Converged" in conv["convergence"].answer  # type: ignore[attr-defined]
    still = _by_id({"train/loss": _s("train/loss", 2 - np.linspace(0, 1.5, N)), "train/lr": _s("train/lr", lr_decay)})
    assert still["convergence"].status == "warn" and "under-trained" in still["convergence"].answer  # type: ignore[attr-defined]
    high = _by_id({"train/loss": _s("train/loss", FALL), "train/lr": _s("train/lr", np.full(N, 1e-3))})
    assert high["convergence"].status == "warn" and "high LR" in high["convergence"].answer  # type: ignore[attr-defined]
    up = np.concatenate([FALL[:40], FALL[39] * np.linspace(1, 3, 40)])
    assert _by_id({"train/loss": _s("train/loss", up)})["convergence"].status == "bad"  # type: ignore[attr-defined]


def test_loss_converged_but_eval_still_improves() -> None:
    a = _by_id(
        {
            "train/loss": _s("train/loss", FALL),
            "train/lr": _s("train/lr", np.linspace(1e-3, 1e-6, N)),
            "val/NDS": _s("val/NDS", np.linspace(0.3, 0.5, N)),
        }
    )
    assert a["convergence"].status == "warn" and "still improves" in a["convergence"].answer  # type: ignore[attr-defined]
