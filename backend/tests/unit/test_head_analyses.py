"""Uncertainty, temporal-memory, loss-interaction and denoising analyses over instrumentation-kit metrics."""

import numpy as np

from model_forensic.analysis.base import AnalysisContext
from model_forensic.analysis.plugins.architecture import DECODER_LAYERS, DecoderLayersParams
from model_forensic.analysis.plugins.heads import (
    LOSS_INTERACTIONS,
    TEMPORAL_MEMORY,
    UNCERTAINTY_HEALTH,
    InteractionParams,
    TemporalParams,
    UncertaintyParams,
)
from model_forensic.domain.findings import Severity
from tests.unit.test_architecture_analyses import STEPS, data, s

RAMP = np.linspace(0, 1, STEPS.size)


def unc(dim: str, nerr: float, corr: float = 0.5, by_range: dict[str, float] | None = None) -> list:
    out = [s(f"val/unc_nerr[dim={dim}]", nerr), s(f"val/unc_corr[dim={dim}]", corr)]
    out += [s(f"val/unc_nerr[dim={dim},range={r}]", v) for r, v in (by_range or {}).items()]
    return out


def titles(res) -> list[str]:
    return [f.title for f in res.findings]


def test_uncertainty_calibrated_is_quiet() -> None:
    res = UNCERTAINTY_HEALTH.run(AnalysisContext(target=data(*unc("x", 1.0), *unc("vel", 0.95))), UncertaintyParams())
    assert not res.findings and res.notes


def test_uncertainty_over_under_flat_and_range() -> None:
    target = data(
        *unc("x", 2.4, by_range={"0-30": 1.0, "60-inf": 2.6}),
        *unc("w", 0.5),
        *unc("vel", 1.0, corr=0.02),
    )
    t = titles(UNCERTAINTY_HEALTH.run(AnalysisContext(target=target), UncertaintyParams()))
    assert "Uncertainty is over-confident for x" in t
    assert "Uncertainty is under-confident for w" in t
    assert "Uncertainty does not rank errors for vel" in t
    assert any(x.startswith("Uncertainty is over-confident at 60-inf") for x in t)
    over = next(
        f
        for f in UNCERTAINTY_HEALTH.run(AnalysisContext(target=target), UncertaintyParams()).findings
        if f.title.endswith("for x")
    )
    assert over.severity == Severity.HIGH


def test_uncertainty_attenuation() -> None:
    target = data(
        *unc("vel", 1.0),
        s("train/unc_scale[dim=vel]", 0.4 + 0.8 * RAMP),
        s("train/unc_abs_err[dim=vel]", 0.5 - 0.02 * RAMP),
        s("train/unc_scale[dim=x]", 0.4 - 0.2 * RAMP),
        s("train/unc_abs_err[dim=x]", 0.4 - 0.2 * RAMP),
    )
    (f,) = [
        f
        for f in UNCERTAINTY_HEALTH.run(AnalysisContext(target=target), UncertaintyParams()).findings
        if "attenuation" in f.title
    ]
    assert f.title == "Loss attenuation on vel" and f.recommendations


def test_uncertainty_skipped_without_metrics() -> None:
    res = UNCERTAINTY_HEALTH.run(AnalysisContext(target=data(s("train/loss", 1.0))), UncertaintyParams())
    assert res.skipped_reason and "unc_nerr" in res.skipped_reason


def seqpos(gain: float) -> list:
    return [s(f"train/loss/bbox[seqpos={p}]", 1.0 - g) for p, g in (("0", 0.0), ("1-3", gain / 2), ("4-inf", gain))]


def test_temporal_memory_benefit_levels() -> None:
    p = TemporalParams()
    assert titles(TEMPORAL_MEMORY.run(AnalysisContext(target=data(*seqpos(0.10))), p)) == [
        "Temporal memory gives 10.0%"
    ]
    assert titles(TEMPORAL_MEMORY.run(AnalysisContext(target=data(*seqpos(0.005))), p)) == [
        "Temporal memory barely helps"
    ]
    res = TEMPORAL_MEMORY.run(AnalysisContext(target=data(*seqpos(-0.05))), p)
    assert res.findings[0].title == "Frames with temporal history are worse than first frames"
    assert res.findings[0].severity == Severity.HIGH


def test_temporal_query_sources() -> None:
    target = data(
        s("train/q_from_memory_frac", 0.04),
        s("train/q_pos_frac[source=propagated]", 0.02),
        s("train/q_pos_frac[source=new]", 0.05),
    )
    t = titles(TEMPORAL_MEMORY.run(AnalysisContext(target=target), TemporalParams()))
    assert t == ["Propagated memory queries are worse candidates than fresh ones", "Memory queries are rarely matched"]
    assert TEMPORAL_MEMORY.run(AnalysisContext(target=data(s("train/loss", 1.0))), TemporalParams()).skipped_reason


def test_loss_interactions_conflict_dominance_negligible() -> None:
    target = data(
        s("train/term_grad_norm[term=bbox]", 8.0),
        s("train/term_grad_norm[term=cls]", 1.0),
        s("train/term_grad_norm[term=aux2d]", 0.05),
        s("train/loss/bbox", 0.3),
        s("train/loss/cls", 0.6),
        s("train/term_grad_cos[a=bbox,b=cls]", -0.45),
        s("train/term_grad_cos[a=bbox,b=aux2d]", 0.3),
    )
    res = LOSS_INTERACTIONS.run(AnalysisContext(target=target), InteractionParams())
    t = titles(res)
    assert any(x.startswith("bbox dominates") for x in t)
    assert "aux2d barely affects the shared module" in t
    (c,) = [f for f in res.findings if "opposite" in f.title]
    assert c.title == "bbox and cls pull the shared module in opposite directions" and c.severity == Severity.HIGH
    assert LOSS_INTERACTIONS.run(AnalysisContext(target=data(s("train/loss", 1.0))), InteractionParams()).skipped_reason


def test_decoder_layers_denoising_branch() -> None:
    series = []
    for k in range(4):
        series.append(s(f"train/loss/bbox[layer={k}]", 1.0 - 0.2 * k))
        series.append(s(f"train/loss/bbox[branch=dn,layer={k}]", (0.05 - 0.005 * k) * np.ones(STEPS.size)))
    res = DECODER_LAYERS.run(AnalysisContext(target=data(*series)), DecoderLayersParams())
    t = titles(res)
    assert "Denoising task is trivial (bbox)" in t
    assert "Denoising branch does not learn (bbox)" in t


# ------------------------------------------------------------------ learned task weights (Kendall)
from model_forensic.analysis.plugins.heads import TASK_WEIGHTING, TaskWeightingParams  # noqa: E402


def test_task_weighting_gave_up_off_and_trusted() -> None:
    target = data(
        s("train/task_log_sigma[task=t,term=vel]", 0.0 + 1.2 * RAMP),
        s("train/uloss/vel[task=t]", 0.8),
        s("train/task_log_sigma[task=t,term=box]", 0.0 + 0.8 * RAMP),
        s("train/uloss/box[task=t]", 1.0 - 0.5 * RAMP),
        s("train/task_log_sigma[task=t,term=cls]", -0.8 * RAMP),
        s("train/uloss/cls[task=t]", 1.0 - 0.4 * RAMP),
    )
    res = TASK_WEIGHTING.run(AnalysisContext(target=target), TaskWeightingParams())
    t = titles(res)
    assert "The model is giving up on vel" in t
    assert "The model down-weights box" in t
    assert "The model increasingly trusts cls" in t
    assert any(x.startswith("vel is effectively switched off") for x in t)
    assert res.findings[0].severity == Severity.HIGH and res.findings[0].recommendations


def test_task_weighting_weights_drive_total_and_quiet() -> None:
    target = data(
        s("train/task_log_sigma[task=t,term=cls]", -0.05 * RAMP),
        s("train/task_log_sigma[task=t,term=box]", -0.05 * RAMP),
        s("train/loss", 2.0 - 1.5 * RAMP),
        s("train/uloss", 2.0 - 0.05 * RAMP),
    )
    t = titles(TASK_WEIGHTING.run(AnalysisContext(target=target), TaskWeightingParams()))
    assert t == ["The total loss fell mostly because the task weights moved"]
    quiet = data(s("train/task_log_sigma[task=t,term=cls]", 0.01), s("train/task_log_sigma[task=t,term=box]", 0.0))
    res = TASK_WEIGHTING.run(AnalysisContext(target=quiet), TaskWeightingParams())
    assert not res.findings and res.notes
    assert TASK_WEIGHTING.run(AnalysisContext(target=data(s("train/loss", 1.0))), TaskWeightingParams()).skipped_reason


def test_decoder_layers_prefer_unweighted_losses() -> None:
    series = [s(f"train/uloss/cls[layer={k},task=t]", 1.0 - 0.2 * k) for k in range(4)]
    series += [s(f"train/loss/cls[layer={k},task=t]", 1.0) for k in range(4)]  # weighted: flat, misleading
    res = DECODER_LAYERS.run(AnalysisContext(target=data(*series)), DecoderLayersParams())
    stmts = [e.statement for f in res.findings for e in f.evidence]
    assert stmts and all("source" not in x for x in stmts)
    assert any("L0=1 " in x or "L0=1," in x for x in stmts) and any("L3=0.4" in x for x in stmts)
