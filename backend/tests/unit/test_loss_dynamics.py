"""Loss dynamics: the LR-schedule loss model (fit, schedule vs change) and the training rate per loss term."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from model_forensic.analysis import loss_dynamics as ld
from model_forensic.analysis.loss_questions import ask
from model_forensic.api.app import create_app
from model_forensic.application.observations import from_dynamics
from model_forensic.domain.metrics import MetricKey, MetricSeries

T = 20_000


def _s(key: str, st: np.ndarray, v: np.ndarray) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(map(float, st)), values=tuple(map(float, v)))


def cosine(total: int = T, warm: int = 500) -> MetricSeries:
    st = np.arange(0, total + 1, 10)
    lr = np.where(st < warm, st / warm, 0.5 * (1 + np.cos(np.pi * (st - warm) / (total - warm))))
    return _s("train/lr", st, lr * 2e-4)


def wsd(total: int = T, decay: float = 0.2, warm: int = 500) -> MetricSeries:
    st = np.arange(0, total + 1, 10)
    d0 = total * (1 - decay)
    lr = np.where(st < warm, st / warm, np.where(st < d0, 1.0, 1 - (st - d0) / (total - d0)))
    return _s("train/lr", st, np.maximum(lr, 0) * 2e-4)


def law(lr: MetricSeries, l0: float = 1.0, a: float = 3.0, al: float = 0.5, c: float = 4e-4, noise: float = 0.01,
        seed: int = 0, key: str = "train/loss") -> MetricSeries:  # fmt: skip
    sch = ld.schedule(lr)
    assert sch is not None
    st = np.arange(0, sch.end + 1, 10).astype(float)
    s1, s2 = sch.at(st)
    v = l0 + a * np.power(np.maximum(s1, 1), -al) - c * s2
    return _s(key, st, v * (1 + noise * np.random.default_rng(seed).standard_normal(v.size)))


def test_schedule_areas_follow_the_definition() -> None:
    sch = ld.schedule(wsd())
    assert sch is not None and sch.warmup_end <= 500
    # S1 grows with the summed LR; S2 stays ~0 until the decay starts, then rises
    i_stable = int(np.searchsorted(sch.steps, 0.7 * T))
    assert sch.s2[i_stable] < 1e-6 < sch.s2[-1]
    assert np.all(np.diff(sch.s1) >= 0)


def test_fit_recovers_the_curve_and_predicts_a_longer_run() -> None:
    lr = cosine()
    out = ld.one_run(law(lr), lr, "train/loss")
    f = out["fit"]
    assert f["usable"] and f["r2"] > 0.99 and abs(f["l0"] - 1.0) < 0.1 and f["c"] > 0
    assert 0 < out["anneal_share"] < 1 and out["longer_1_5x"] < f["end_fitted"]
    assert "fits the LR model" in out["sentence"] and "LR decay took" in out["sentence"]


def test_a_gap_that_is_only_the_schedule() -> None:
    """Same model (same law), different schedules: the visible gap is the schedule, not the change."""
    b_lr, c_lr = cosine(), wsd()
    e = ld.schedule_effect(law(b_lr), b_lr, law(c_lr, seed=1), c_lr, "train/loss")
    assert e["verdict"] == "mostly_schedule" and abs(e["equal_schedule_gap"]) < 0.01 < abs(e["observed_gap"])
    assert "of the gap is the LR schedule" in e["sentence"]


def test_a_real_gain_holds_on_one_schedule_and_same_schedule_is_said() -> None:
    b_lr, c_lr = cosine(), wsd()
    e = ld.schedule_effect(law(b_lr), b_lr, law(c_lr, l0=0.9, seed=1), c_lr, "train/loss")
    assert e["verdict"] == "holds" and e["equal_schedule_gap"] > 0.05
    same = ld.schedule_effect(law(b_lr), b_lr, law(b_lr, l0=0.95, seed=1), b_lr, "train/loss")
    assert same["verdict"] == "same_schedule" and "lower than the baseline" in same["sentence"]


def test_nothing_is_read_from_a_curve_the_model_does_not_describe() -> None:
    lr = cosine()
    st = np.arange(0, T + 1, 10)
    noise = _s("train/loss", st, 1 + 0.3 * np.random.default_rng(3).standard_normal(st.size) ** 2)
    out = ld.one_run(noise, lr, "train/loss")
    assert out["fit"] is not None and not out["fit"]["usable"] and "nothing is read" in out["sentence"]
    assert ld.problems({"fits": [out], "effects": []}) == []


def _terms(slow: float = 1.0, flat: bool = False) -> dict[str, MetricSeries]:
    st = np.arange(0, 2000, 10).astype(float)
    x = st / st[-1]
    rng = np.random.default_rng(0)

    def curve(rate: float) -> np.ndarray:
        return (0.2 + np.exp(-rate * x)) * np.exp(rng.normal(0, 0.01, x.size))

    out = {
        "train/loss/cls": _s("train/loss/cls", st, curve(4)),
        "train/loss/bbox": _s("train/loss/bbox", st, curve(4)),
        "train/loss/velocity": _s("train/loss/velocity", st, np.full(x.size, 0.9) * np.exp(rng.normal(0, 0.005, x.size))
                                  if flat else curve(4 / slow)),
    }  # fmt: skip
    out["train/loss"] = _s("train/loss", st, sum(np.asarray(s.values) for s in out.values()))
    return out


def test_training_rates_find_the_starved_term() -> None:
    rates = {r.key: r for r in ld.training_rates(_terms(slow=6.0))}
    v = rates["train/loss/velocity"]
    assert v.group == "terms" and v.status == "lagging" and v.rate_end > 1.25 and v.lag_from is not None
    assert rates["train/loss/cls"].status in ("normal", "leading")
    assert "train/loss" not in rates  # the total is not a term
    flat = {r.key: r for r in ld.training_rates(_terms(flat=True))}
    assert flat["train/loss/velocity"].status == "not_learning"
    even = ld.training_rates(_terms(slow=1.0))
    assert all(r.status == "normal" for r in even)


def test_health_question_and_observations() -> None:
    a = next(x for x in ask(_terms(slow=6.0)) if x.id == "term_rate")
    assert a.status == "warn" and "train/loss/velocity" in a.keys and a.param == "loss_dynamics.rate_lag"
    assert next(x for x in ask(_terms(flat=True)) if x.id == "term_rate").status == "bad"
    assert next(x for x in ask(_terms(slow=1.0)) if x.id == "term_rate").status == "ok"
    b_lr, c_lr = cosine(), wsd()
    rep = ld.report({"train/loss": law(c_lr, seed=1)}, c_lr, {"train/loss": law(b_lr)}, b_lr)
    obs = from_dynamics(rep)
    warn = [o for o in obs if o.status == "warn"]
    assert warn and warn[0].source == "dynamics" and warn[0].cause == "schedule" and warn[0].evidence == ("train/loss",)
    assert any("mostly the LR schedule" in r for r in rep["readings"])
    no_lr = ld.report(_terms(), None)
    assert not no_lr["lr_logged"] and no_lr["fits"] == [] and "No learning-rate series" in no_lr["readings"][0]


def test_api_cli_and_dossier(container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from model_forensic.cli import main as cli

    run, base = "local:cand-radar-8f", "local:baseline-cl4f-s0"
    c = TestClient(create_app(container))
    d = c.get(f"/api/v1/runs/{run}/loss-dynamics", params={"baseline": base}).json()
    assert d["lr_logged"] and d["fits"] and d["effects"] and d["rates"]
    assert any(r["key"] == "train/loss/velocity" and r["status"] == "lagging" for r in d["rates"])
    assert c.get(f"/api/v1/runs/{run}/loss-dynamics", params={"stage": "nope"}).status_code == 400
    obs = c.get(f"/api/v1/runs/{run}/observations").json()["observations"]
    assert any(o["id"] == "health:term_rate" and o["status"] == "warn" for o in obs)
    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)
    res = CliRunner().invoke(cli.app, ["dynamics", "--run", run, "--baseline", base])
    assert res.exit_code == 0, res.output
    assert "What the LR schedule did" in res.output and "train/loss/velocity" in res.output
    from model_forensic.application.dossier import build

    text = build(container.service, container.knowledge, container.docs, run, base)
    assert "Loss dynamics" in text and "[E-S1]" in text
