"""Loss drift: progression differences per loss, shape notes, readings, schedule alignment, API and CLI."""

from __future__ import annotations

import numpy as np

from model_forensic.analysis.loss_drift import loss_drift
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 200
rng = np.random.default_rng(0)


def _s(key: str, v: np.ndarray, span: float = 1.0) -> MetricSeries:
    return MetricSeries(
        key=MetricKey.parse(key), steps=tuple(float(i) * span for i in range(len(v))), values=tuple(map(float, v))
    )


def _curve(rate: float = 4.0, floor: float = 0.2, noise: float = 0.01) -> np.ndarray:
    x = np.linspace(0, 1, N)
    return (np.exp(-rate * x) + floor) * np.exp(rng.normal(0, noise, N))


def _rows(out: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {r["key"]: r for r in out["rows"]}


def test_same_progression_and_a_drift_from_the_start() -> None:
    base = {"train/loss/cls": _s("train/loss/cls", _curve()), "train/loss/bbox": _s("train/loss/bbox", _curve())}
    same = loss_drift(base, {k: _s(k, np.asarray(v.values) * 1.001) for k, v in base.items()})
    assert all(r["outcome"] == "same" for r in same["rows"]) and "progress like the baseline" in same["summary"]
    cand = dict(base)
    cand["train/loss/bbox"] = _s("train/loss/bbox", _curve() * 1.3)
    out = loss_drift(base, cand)
    r = _rows(out)["train/loss/bbox"]
    assert r["outcome"] == "behind" and r["diverge_fraction"] < 0.05
    assert out["rows"][0]["key"] == "train/loss/bbox"  # ranked first
    assert any("first steps" in x for x in out["readings"])


def test_closing_gap_slower_convergence_and_clusters() -> None:
    x = np.linspace(0, 1, N)
    base = {f"train/loss/{t}": _s(f"train/loss/{t}", _curve()) for t in ("a", "b", "c")}
    late = np.where(x > 0.5, 1 + 0.4 * (x - 0.5), 1.0)  # two losses rise from mid-training
    cand = {
        "train/loss/a": _s("train/loss/a", np.asarray(base["train/loss/a"].values) * late),
        "train/loss/b": _s("train/loss/b", np.asarray(base["train/loss/b"].values) * late),
        "train/loss/c": _s("train/loss/c", _curve(rate=1.5)),  # slower, same end after a long tail? (ends higher)
    }
    out = loss_drift(base, cand)
    rows = _rows(out)
    assert rows["train/loss/a"]["outcome"] == "behind" and 0.45 < rows["train/loss/a"]["diverge_fraction"] < 0.7
    assert any("drift together" in x for x in out["readings"])
    assert any(n.startswith("converges slower") for n in rows["train/loss/c"]["shape_notes"])


def test_schedules_of_different_length_are_aligned_by_fraction() -> None:
    base = {"train/loss": _s("train/loss", _curve(), span=1.0)}
    cand = {"train/loss": _s("train/loss", _curve(), span=2.0)}  # twice as many steps, same progression
    out = loss_drift(base, cand)
    assert out["aligned_by"] == "fraction of the schedule" and out["rows"][0]["outcome"] == "same"


def test_api_and_cli(container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli

    c = TestClient(create_app(container))
    d = c.get(
        "/api/v1/compare/loss-drift", params={"baseline": "local:baseline-cl4f-s0", "candidate": "local:cand-radar-8f"}
    ).json()
    assert d["rows"] and d["readings"] and d["summary"]
    monkeypatch.setattr(cli, "_c", lambda _cfg=None: container)
    res = CliRunner().invoke(
        cli.app, ["drift", "--baseline", "local:baseline-cl4f-s0", "--candidate", "local:cand-radar-8f"]
    )
    assert res.exit_code == 0 and "losses" in res.output
