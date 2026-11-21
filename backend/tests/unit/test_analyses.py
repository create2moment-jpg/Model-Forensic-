from datetime import datetime, timezone

import numpy as np

from model_forensic.analysis.base import AnalysisContext, RunData
from model_forensic.analysis.config_diff import ConfigDiffer
from model_forensic.analysis.plugins import config_attribution, loss_forensics, regression, training_dynamics
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.findings import EpistemicLabel, Severity
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef, RunStatus


def run(rid: str, **kw) -> Run:
    return Run(
        ref=RunRef(backend="t", run_id=rid),
        name=rid,
        status=RunStatus.COMPLETED,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
        **kw,
    )


def series(key: str, values, steps=None) -> MetricSeries:
    values = list(map(float, values))
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(steps or range(len(values))), values=tuple(values))


def data(rid: str, *ss: MetricSeries, **kw) -> RunData:
    return RunData(run=run(rid, **kw), series={s.key: s for s in ss})


def test_training_dynamics_detects_nan_and_divergence() -> None:
    v = np.r_[np.linspace(2, 1, 60), np.linspace(1, 30, 30), [np.nan] * 10]
    ctx = AnalysisContext(target=data("a", series("train/loss", v)))
    res = training_dynamics.PLUGIN.run(ctx, training_dynamics.Params())
    titles = {f.title for f in res.findings}
    assert "Non-finite loss (NaN/Inf)" in titles and "Training diverged" in titles
    nan = next(f for f in res.findings if f.title.startswith("Non-finite"))
    assert nan.severity == Severity.CRITICAL and nan.label == EpistemicLabel.OBSERVED
    assert nan.provenance.analysis == "training_dynamics" and nan.provenance.runs == (RunRef(backend="t", run_id="a"),)


def test_training_dynamics_clean_curve_has_no_findings() -> None:
    rng = np.random.default_rng(1)
    v = 0.5 + 1.5 * np.exp(-np.arange(200) / 40) + 0.005 * rng.standard_normal(200)
    res = training_dynamics.PLUGIN.run(
        AnalysisContext(target=data("a", series("train/loss", v))), training_dynamics.Params()
    )
    assert [f.title for f in res.findings if f.severity.rank >= 2] == []


def test_training_dynamics_overfitting() -> None:
    tr = series("train/loss", np.linspace(2, 0.5, 100), steps=range(0, 1000, 10))
    val = series("val/loss", [1.5, 1.2, 1.0, 0.95, 1.0, 1.1, 1.2, 1.3], steps=range(100, 900, 100))
    res = training_dynamics.PLUGIN.run(AnalysisContext(target=data("a", tr, val)), training_dynamics.Params())
    f = next(f for f in res.findings if f.title.startswith("Overfitting"))
    assert f.recommendations and f.recommendations[0].label == EpistemicLabel.RECOMMENDED


def test_finding_ids_are_deterministic() -> None:
    v = np.r_[np.linspace(2, 1, 60), [np.nan] * 5]
    ctx = AnalysisContext(target=data("a", series("train/loss", v)))
    a = training_dynamics.PLUGIN.run(ctx, training_dynamics.Params())
    b = training_dynamics.PLUGIN.run(ctx, training_dynamics.Params())
    assert [f.id for f in a.findings] == [f.id for f in b.findings]


def test_loss_forensics_dominant_and_dead_terms() -> None:
    n = 50
    ctx = AnalysisContext(
        target=data(
            "a",
            series("train/loss/cls", np.linspace(5, 4, n)),
            series("train/loss/bbox", np.linspace(1, 0.5, n)),
            series("train/loss/aux", np.zeros(n)),
        )
    )
    res = loss_forensics.PLUGIN.run(ctx, loss_forensics.Params())
    titles = [f.title for f in res.findings]
    assert "Dominant loss term" in titles and "Dead loss term: loss/aux" in titles


def _bc(base_vals: dict[str, float], cand_vals: dict[str, float], reps: list[dict[str, float]] = ()):
    def mk(rid, vals):
        return data(rid, *[series(k, [v * 0.9, v]) for k, v in vals.items()])

    return mk("base", base_vals), mk("cand", cand_vals), [mk(f"rep{i}", r) for i, r in enumerate(reps)]


def test_regression_localizes_to_range_slice() -> None:
    base = {
        "val/recall": 0.60,
        "val/recall[range=0-30]": 0.78,
        "val/recall[range=30-60]": 0.62,
        "val/recall[range=60-inf]": 0.41,
    }
    cand = {
        "val/recall": 0.57,
        "val/recall[range=0-30]": 0.779,
        "val/recall[range=30-60]": 0.617,
        "val/recall[range=60-inf]": 0.33,
    }
    b, c, _ = _bc(base, cand)
    res = regression.PLUGIN.run(AnalysisContext(target=c, baseline=b), regression.RegressionParams())
    f = next(f for f in res.findings if f.title.startswith("Regression in val/recall"))
    assert "range=60-inf" in f.title
    assert any("Fewer than 2 baseline replicates" in lim for lim in f.limitations)


def test_regression_hidden_in_slice() -> None:
    base = {"val/mAP": 0.55, "val/mAP[class=car]": 0.66, "val/mAP[class=cyclist]": 0.41}
    cand = {"val/mAP": 0.551, "val/mAP[class=car]": 0.67, "val/mAP[class=cyclist]": 0.37}
    b, c, _ = _bc(base, cand)
    res = regression.PLUGIN.run(AnalysisContext(target=c, baseline=b), regression.RegressionParams())
    assert any(f.title.startswith("Hidden regression in val/mAP slice class=cyclist") for f in res.findings)


def test_replicates_suppress_noise_level_deltas() -> None:
    base = {"val/NDS": 0.620}
    cand = {"val/NDS": 0.612}  # -1.3%: above threshold, but within seed noise below
    reps = [{"val/NDS": 0.611}, {"val/NDS": 0.628}]
    b, c, r = _bc(base, cand, reps)
    no_reps = regression.PLUGIN.run(AnalysisContext(target=c, baseline=b), regression.RegressionParams())
    with_reps = regression.PLUGIN.run(AnalysisContext(target=c, baseline=b, baseline_replicates=r), regression.RegressionParams())
    assert any("NDS" in f.title for f in no_reps.findings)
    assert not any("NDS" in f.title for f in with_reps.findings)


def test_config_attribution_is_hypothesis_never_fact() -> None:
    ra, rb = RunRef(backend="t", run_id="base"), RunRef(backend="t", run_id="cand")
    diff = ConfigDiffer().diff(
        ra,
        [
            ConfigurationSnapshot(
                run=ra,
                source="config.yaml",
                data={"model": {"fusion": {"modalities": ["camera"]}}, "optimizer": {"lr": 1}},
            )
        ],
        rb,
        [
            ConfigurationSnapshot(
                run=rb,
                source="config.yaml",
                data={"model": {"fusion": {"modalities": ["camera", "radar"]}}, "optimizer": {"lr": 2}},
            )
        ],
    )
    b, c, _ = _bc({"val/recall[range=60-inf]": 0.4, "val/NDS": 0.6}, {"val/recall[range=60-inf]": 0.3, "val/NDS": 0.55})
    res = config_attribution.PLUGIN.run(
        AnalysisContext(target=c, baseline=b, config_diff=diff), config_attribution.Params()
    )
    (f,) = res.findings
    assert f.label == EpistemicLabel.HYPOTHESIZED
    assert all(h.label == EpistemicLabel.HYPOTHESIZED for h in f.hypotheses)
    assert f.recommendations[0].controlled_variable == "model.fusion.modalities"
    assert any("Correlation only" in lim for lim in f.limitations)
