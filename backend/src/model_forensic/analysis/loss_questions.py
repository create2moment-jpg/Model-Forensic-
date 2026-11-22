"""Training health questions: the questions to ask of a run's losses, each answered from its series.

Deterministic (no LLM). Each answer says what was found (with numbers), which series show it (to plot), where to
look in the UI, and what to try when the answer is a problem: the step from "something is sub-optimal" to "the next
experiment". Questions without the data they need say what to log instead of guessing.

Status: ``bad`` (a problem), ``warn`` (worth a look), ``ok``, ``na`` (the run does not log what it needs).
Layer indices are read as logged (``layer=0`` first); which index is the first decoder layer is project knowledge
(harness task ``head_and_queries``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from model_forensic.analysis import signal
from model_forensic.domain.metrics import MetricSeries

Series = Mapping[str, MetricSeries]


@dataclass
class Answer:
    id: str
    area: str
    question: str
    status: str  # bad | warn | ok | na
    answer: str
    keys: list[str] = field(default_factory=list)
    look: str = ""
    ideas: list[str] = field(default_factory=list)
    why: str = ""

    def view(self) -> dict[str, Any]:
        return asdict(self)


AREAS = ("trajectory", "terms", "layers", "weighting", "optimisation", "features", "queries", "baseline")


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _parse(k: str) -> tuple[str, dict[str, str]]:
    i = k.find("[")
    if i < 0 or not k.endswith("]"):
        return k, {}
    return k[:i], dict(p.split("=", 1) for p in k[i + 1 : -1].split(",") if "=" in p)


def _is_loss(k: str) -> bool:
    fam = _parse(k)[0].lower()
    return "loss" in fam and not re.search(r"sigma|log_?var|uncert|weight", fam)


def _is_val(k: str) -> bool:
    return bool(re.match(r"^(val|valid|validation|test|eval)[/_.]", k, re.I))


def _fin(s: MetricSeries) -> tuple[np.ndarray, np.ndarray]:
    return s.finite()


def _end(s: MetricSeries) -> float:
    _, v = _fin(s)
    return float(signal.ema(v, 0.3)[-1]) if v.size else math.nan


def _at(s: MetricSeries, frac: float) -> float:
    _, v = _fin(s)
    if not v.size:
        return math.nan
    sm = signal.ema(v, 0.3)
    return float(sm[min(len(sm) - 1, int(frac * (len(sm) - 1)))])


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _num(x: float) -> str:
    return "—" if not math.isfinite(x) else f"{x:.4g}"


def _total(series: Series) -> str | None:
    """The training total loss: ``train/loss`` (or the shortest train-side loss name without dimensions)."""
    plain = [k for k in series if _is_loss(k) and "[" not in k and not _is_val(k)]
    for k in plain:
        if k.lower() in ("train/loss", "loss", "train_loss", "train/total_loss", "train/loss_total"):
            return k
    return min(plain, key=len) if plain else None


def _terms(series: Series, total: str | None) -> list[str]:
    return sorted(k for k in series if _is_loss(k) and "[" not in k and not _is_val(k) and k != total)


def _layer_families(series: Series) -> dict[str, list[tuple[int, str]]]:
    """Loss families whose only dimension is a layer index → [(layer, key)] sorted by layer."""
    out: dict[str, list[tuple[int, str]]] = {}
    for k in series:
        fam, dims = _parse(k)
        if not _is_loss(k) or len(dims) != 1:
            continue
        d, v = next(iter(dims.items()))
        if re.fullmatch(r"layer|lvl|dec_layer|decoder_layer", d, re.I) and v.lstrip("-").isdigit():
            out.setdefault(fam, []).append((int(v), k))
    return {f: sorted(v) for f, v in out.items() if len(v) >= 2}


def _diverging(s: MetricSeries) -> tuple[str, str] | None:
    """('bad'|'warn', text) when a loss ends well above its minimum and still rises; None when it does not."""
    st, v = _fin(s)
    if v.size < 8:
        return None
    sm = signal.ema(v, 0.2)
    i = int(np.argmin(sm))
    lo, end = float(sm[i]), float(sm[-1])
    tail_s, tail_v = st[-max(4, v.size // 4) :], sm[-max(4, v.size // 4) :]
    rising = signal.normalized_slope(tail_s, tail_v) > 0
    rel = (end - lo) / (abs(lo) or 1e-12)
    if rel > 0.25 and rising:
        return "bad", f"ends {_pct(rel)} above its minimum (step {st[i]:g}) and still rises"
    if rel > 0.05 and rising and i < 0.9 * (v.size - 1):
        return "warn", f"creeps up: {_pct(rel)} above its minimum (step {st[i]:g})"
    return None


# ── the questions ──────────────────────────────────────────────────────────────────────────────────────────────────


def _q_nonfinite(series: Series, total: str | None) -> Answer:
    bad = [k for k in series if _is_loss(k) and any(not math.isfinite(x) for x in series[k].values)]
    return Answer(
        "nonfinite", "trajectory", "Did any loss produce NaN / Inf?",
        "bad" if bad else "ok",
        f"{len(bad)} loss series have NaN/Inf: {', '.join(bad[:6])}" if bad else "No NaN / Inf in any loss.",
        bad[:6] or ([total] if total else []),
        "Metrics → the chart shows a red NaN/Inf badge; Logs tab around that step.",
        ["Lower the LR or lengthen warm-up", "Enable / tighten gradient clipping", "Check fp16 overflow (loss scaling)",
         "Look for empty targets or division by a zero normaliser (no matched boxes) in that batch"],
        "A single non-finite step can poison the weights; later values may look normal while the model is damaged.",
    )  # fmt: skip


def _q_diverge(series: Series, total: str | None) -> Answer:
    hits: list[tuple[str, str, str]] = []
    for k in [total, *_terms(series, total)] if total else _terms(series, total):
        if k and (d := _diverging(series[k])):
            hits.append((k, *d))
    status = "bad" if any(h[1] == "bad" for h in hits) else "warn" if hits else "ok" if total else "na"
    if status == "na":
        text = "No training loss is logged (e.g. train/loss)."
    elif hits:
        text = "; ".join(f"{k} {t}" for k, _, t in hits[:5])
    else:
        text = f"{total} and its terms keep falling or stay near their minimum."
    return Answer(
        "diverge", "trajectory", "Is the loss diverging?", status, text,
        [h[0] for h in hits[:6]] or ([total] if total else []),
        "Metrics → Losses row → All losses / train losses; zoom from the minimum to the end — the Section summary "
        "gives start, end and change per loss.",
        ["Lower the peak LR or lengthen warm-up", "Check the LR schedule at the step where it turns (a restart? a "
         "stage change?)", "Gradient clipping; check grad norm at that step", "If only one term diverges: its weight, "
         "its targets, or a head fighting the others"],
        "A loss that turns upward means the optimisation is no longer improving the objective — everything after that "
        "point is wasted or harmful compute.",
    )  # fmt: skip


def _q_spikes(series: Series, total: str | None) -> Answer:
    if not total:
        return Answer("spikes", "trajectory", "Are there loss spikes?", "na", "No training loss logged.")
    st, v = _fin(series[total])
    sp = signal.detect_spikes(st, v, z=8.0, min_rel_jump=0.4)
    status = "bad" if len(sp) >= 5 else "warn" if sp else "ok"
    text = (f"{len(sp)} spikes in {total} (steps {', '.join(f'{s.step:g}' for s in sp[:5])})" if sp
            else f"No spikes in {total}.")  # fmt: skip
    return Answer(
        "spikes", "trajectory", "Are there loss spikes?", status, text, [total],
        "Metrics → the chart of the total; zoom around a spike; compare grad norm and LR at that step.",
        ["Gradient clipping (or a lower clip value)", "Lower LR / longer warm-up", "Inspect the batches at those steps "
         "(corrupt samples, extreme boxes, empty frames)", "fp16: dynamic loss scaling"],
        "Repeated spikes slow convergence and can push the model into a worse basin even when the curve recovers.",
    )  # fmt: skip


def _q_plateau(series: Series, total: str | None) -> Answer:
    if not total:
        return Answer(
            "plateau", "trajectory", "Is it still learning, or did it plateau early?", "na", "No training loss logged."
        )
    _, v = _fin(series[total])
    if v.size < 10:
        return Answer(
            "plateau", "trajectory", "Is it still learning, or did it plateau early?", "na", "Too few points."
        )
    sm = signal.ema(v, 0.2)
    drop = float(sm[0] - sm[-1]) or 1e-12
    k = next((i for i in range(len(sm)) if sm[i] - sm[-1] < 0.05 * drop), len(sm) - 1)
    at = k / (len(sm) - 1)
    gain = signal.relative_improvement(v, 0.2)
    if gain > 0.05:
        st, text = (
            "warn",
            f"Still improving at the end ({_pct(gain)} over the last 20%): a longer schedule likely helps.",
        )
    elif at < 0.5:
        st, text = (
            "warn",
            f"Plateau from {at:.0%} of training: the remaining {1 - at:.0%} bought < 5% of the loss drop.",
        )
    else:
        st, text = "ok", f"Converges late ({at:.0%} of training) and flattens at the end — the budget fits."
    return Answer(
        "plateau", "trajectory", "Is it still learning, or did it plateau early?", st, text, [total],
        "Metrics → total loss with the LR chart (Metrics row → learning rate); zoom the last half.",
        ["Still learning: train longer, or a higher LR with the same budget", "Early plateau: decay the LR at the "
         "plateau (or a cosine schedule), more capacity / data, or stop earlier and save compute",
         "Compare the LR at the plateau with its peak — a schedule-bound plateau ends with the LR"],
        "Where the curve flattens tells whether the run is limited by the schedule, the model or the data.",
    )  # fmt: skip


def _q_convergence(series: Series, total: str | None) -> Answer:
    """Converged, still learning, stalled at a high LR, or unstable — from the loss, the LR and evaluation together."""
    q = "Has the model converged?"
    if not total:
        return Answer("convergence", "trajectory", q, "na", "No training loss logged.")
    _, v = _fin(series[total])
    if v.size < 10:
        return Answer("convergence", "trajectory", q, "na", f"{total} has too few points.")
    sm = signal.ema(v, 0.2)
    drop = float(sm[0] - sm[-1])
    if drop <= 0 or _diverging(series[total]):
        return Answer(
            "convergence", "trajectory", q, "bad",
            f"Not converging: {total} ends {'above its start' if drop <= 0 else 'above its minimum and rises'}.",
            [total], "Metrics → total loss; the divergence and spike questions above.",
            ["Fix the optimisation first (LR, warm-up, clipping) — convergence is meaningless while it diverges"],
            "A run that does not converge cannot be compared on its final numbers.",
        )  # fmt: skip
    reach = {p: next(i for i in range(len(sm)) if sm[0] - sm[i] >= p * drop) / (len(sm) - 1) for p in (0.5, 0.9)}
    last = float(sm[int(0.8 * (len(sm) - 1))] - sm[-1]) / drop  # share of the whole drop made in the last 20%
    lr = next((k for k in series if re.search(r"(^|[/_.])(lr|learning_rate)($|[/_.\[])", k, re.I)), None)
    lr_end = None
    if lr:
        _, lv = _fin(series[lr])
        lr_end = float(lv[-1] / (lv.max() or 1e-12)) if lv.size else None
    evalk = next((k for k in ("val/NDS", "val/mAP", "val/AMOTA", "val/AP") if k in series), None)
    eval_gain = None
    if evalk:
        a, b = _at(series[evalk], 0.8), _at(series[evalk], 1.0)
        eval_gain = (b - a) / (abs(a) or 1e-12)
    facts = f"Half of the loss drop by {reach[0.5]:.0%} of training, 90% by {reach[0.9]:.0%}; the last 20% made {last:.0%} of it"
    if lr_end is not None:
        facts += f"; the LR ends at {lr_end:.0%} of its peak"
    if eval_gain is not None:
        facts += f"; {evalk} changed {_pct(eval_gain)} in the last 20%"
    if last > 0.1:
        status = "warn"
        verdict = ("Still learning although the LR has decayed — under-trained: a longer schedule should pay."
                   if lr_end is not None and lr_end < 0.1 else "Still learning — not converged: more steps (or a decay "
                   "at the end) should lower the loss further.")  # fmt: skip
    elif eval_gain is not None and eval_gain > 0.01:
        status = "warn"
        verdict = (f"The loss has converged but {evalk} still improves — the loss understates what more steps would "
                   "buy; judge convergence by evaluation.")  # fmt: skip
    elif lr_end is not None and lr_end > 0.3:
        status = "warn"
        verdict = ("Stalled at a high LR — the loss sits at the noise floor of this LR; decaying it would likely lower "
                   "the loss (a schedule-bound plateau, not convergence).")  # fmt: skip
    else:
        status = "ok"
        verdict = "Converged under this schedule — the end of the run adds little."
    return Answer(
        "convergence", "trajectory", q, status, f"{verdict} {facts}.", [k for k in (total, lr, evalk) if k],
        "Metrics → total loss, learning rate and the headline evaluation metric over the same steps (zoom the last "
        "20%; the Section summary gives the change).",
        ["Under-trained: longer schedule at the same peak LR", "Stalled at a high LR: cosine / step decay, or a lower "
         "final LR", "Converged early: shorten the run, or spend the budget on capacity / data", "Loss converged but "
         "eval still moves: evaluate more often and pick the checkpoint by eval"],
        "Converged is relative to the schedule: a lower LR or more steps often moves the 'converged' loss further; "
        "and the loss can converge while evaluation still changes.",
    )  # fmt: skip


def _q_oscillation(series: Series, total: str | None) -> Answer:
    if not total:
        return Answer("oscillation", "trajectory", "Does the loss oscillate?", "na", "No training loss logged.")
    _, v = _fin(series[total])
    osc = signal.oscillation_index(signal.tail(v, 0.4)) if v.size >= 10 else 0.0
    return Answer(
        "oscillation", "trajectory", "Does the loss oscillate?", "warn" if osc > 0.45 else "ok",
        f"Oscillation index {osc:.2f} in the last 40% ({'zig-zag' if osc > 0.45 else 'smooth descent'}).", [total],
        "Metrics → total loss with smoothing 0; compare runs with different LR / batch size.",
        ["Lower LR or larger effective batch (gradient accumulation)", "Check data order / sampler (alternating "
         "datasets or sensor setups per batch)", "EMA of weights for evaluation"],
        "Oscillation late in training means the step size is too big for the basin the model is in.",
    )  # fmt: skip


def _q_overfit(series: Series, total: str | None) -> Answer:
    val = next((k for k in series if _is_val(k) and _is_loss(k) and "[" not in k), None)
    q = "Is it overfitting (val loss rising while train loss falls)?"
    if not val or not total:
        return Answer("overfit", "trajectory", q, "na", "No validation loss logged (val/loss) — log it to see the gap.",
                      look="Metrics → Losses row → val losses.")  # fmt: skip
    sv, vv = _fin(series[val])
    if vv.size < 4:
        return Answer("overfit", "trajectory", q, "na", f"{val} has too few points.")
    sm = signal.ema(vv, 0.3)
    i = int(np.argmin(sm))
    rise = (sm[-1] - sm[i]) / (abs(sm[i]) or 1e-12)
    train_falls = _at(series[total], 1.0) < _at(series[total], 0.6)
    gap = _end(series[val]) / (_end(series[total]) or 1e-12)
    if rise > 0.03 and train_falls and i < len(sm) - 1:
        st, text = "bad", f"{val} is {_pct(rise)} above its best (step {sv[i]:g}) while {total} still falls."
    elif gap > 1.5:
        st, text = "warn", f"Val / train loss ratio {gap:.2f} at the end — a large generalisation gap."
    else:
        st, text = "ok", f"{val} stays near its best; val / train ratio {gap:.2f}."
    return Answer(
        "overfit", "trajectory", q, st, text, [total, val],
        "Metrics → One chart with train and val loss; evaluation metrics over the same steps.",
        ["Pick the checkpoint at the val minimum (check the checkpoint monitor)", "More augmentation / regularisation "
         "(weight decay, drop path)", "More or more varied data (sensor setups, scenes)", "Shorter schedule"],
        "Past the val minimum, training fits the training set at the cost of everything else.",
    )  # fmt: skip


def _q_term_rising(series: Series, total: str | None) -> Answer:
    terms = _terms(series, total)
    q = "Does a loss term get worse while the total improves?"
    if not terms:
        return Answer("term_rising", "terms", q, "na", "Only a total loss is logged — log the terms (cls, bbox, …).")
    total_falls = _at(series[total], 1.0) < _at(series[total], 0.5) if total else True
    rising = [k for k in terms if _diverging(series[k]) is not None]
    st = "bad" if rising and total_falls else "warn" if rising else "ok"
    text = (f"{', '.join(rising[:5])} rise{'s' if len(rising) == 1 else ''} while the total "
            f"{'falls' if total_falls else 'does not'} — another term is bought at its cost." if rising
            else f"All {len(terms)} terms fall with the total.")  # fmt: skip
    return Answer(
        "term_rising", "terms", q, st, text, rising[:6] or terms[:6],
        "Metrics → Losses row → train losses (One chart, Normalise on: who falls how fast).",
        ["Rebalance the weights (or the uncertainty weighting's init / clamps)", "Check the term's targets in this data "
         "(e.g. no velocity labels for some sources)", "Separate LR / warm-up for the head that loses",
         "Gradient conflict between heads: try gradient surgery / per-head normalisation"],
        "Multi-task training trades terms against each other; a rising term is a head the model gave up on.",
    )  # fmt: skip


def _q_term_dominates(series: Series, total: str | None) -> Answer:
    terms = [k for k in _terms(series, total) if math.isfinite(_end(series[k])) and _end(series[k]) > 0]
    q = "Does one term dominate the loss?"
    if len(terms) < 2:
        return Answer("term_share", "terms", q, "na", "Fewer than two loss terms logged.")
    ends = {k: _end(series[k]) for k in terms}
    tot = sum(ends.values())
    k, top = max(ends.items(), key=lambda kv: kv[1])
    share = top / tot
    return Answer(
        "term_share", "terms", q, "warn" if share > 0.7 else "ok",
        f"{k} is {share:.0%} of the summed terms at the end" + (" — it drives the gradient." if share > 0.7 else "."),
        sorted(ends, key=lambda x: -ends[x])[:6],
        "Metrics → Losses row → train losses (One chart).",
        ["Is the share intended? compare with the weights in the config (harness task loss_study)", "Normalise per "
         "term (by matched boxes) or rebalance weights", "Check the term's scale (L1 in metres vs focal loss)"],
        "The largest term sets most of the gradient; small terms may barely train.",
    )  # fmt: skip


def _q_term_flat(series: Series, total: str | None) -> Answer:
    terms = _terms(series, total)
    q = "Is a loss term not learning at all (flat from the start)?"
    if not terms:
        return Answer("term_flat", "terms", q, "na", "No loss terms logged.")
    flat = [
        k for k in terms if abs(_at(series[k], 1.0) - _at(series[k], 0.0)) / (abs(_at(series[k], 0.0)) or 1e-12) < 0.01
    ]
    return Answer(
        "term_flat", "terms", q, "warn" if flat else "ok",
        f"Flat over the whole run: {', '.join(flat[:5])}." if flat else "Every term moves.",
        flat[:6], "Metrics → the term's chart (Ignore spikes on); its weight in Setup / Configs.",
        ["Is the term enabled (weight > 0) and does it get targets?", "Frozen module behind it? (harness task backbone)",
         "A term that is constant may be logged before it is computed"],
        "A flat term either does nothing or is not connected — both waste a head or hide a bug.",
    )  # fmt: skip


def _q_layers_diverge(series: Series, _total: str | None) -> Answer:
    fams = _layer_families(series)
    q = "Is the layer refinement diverging (later decoder layers worse than earlier ones)?"
    if not fams:
        return Answer("layers_diverge", "layers", q, "na", "No per-layer losses logged (…[layer=k]) — log the auxiliary "
                      "losses per decoder layer to see the refinement.")  # fmt: skip
    worse: list[str] = []
    keys: list[str] = []
    for fam, layers in fams.items():
        ends = [(li, _end(series[k]), k) for li, k in layers]
        best = min(ends, key=lambda x: x[1])
        last = ends[-1]
        if last[1] > best[1] * 1.02 and last[0] != best[0]:
            worse.append(f"{fam}: layer {last[0]} ends {_pct(last[1] / best[1] - 1)} above layer {best[0]}")
            keys += [k for _, k in layers]
        rising = [li for li, k in layers if (d := _diverging(series[k])) is not None and d[0] == "bad"]
        if rising:
            worse.append(f"{fam}: layers {', '.join(map(str, rising))} diverge over training")
            keys += [k for _, k in layers]
    return Answer(
        "layers_diverge", "layers", q, "bad" if worse else "ok",
        "; ".join(worse[:4]) if worse else f"In {len(fams)} per-layer families the last layer is the best or within 2%.",
        list(dict.fromkeys(keys))[:12] or [k for _, k in next(iter(fams.values()))][:12],
        "Metrics → Slice breakdowns → the family's chart and its per-layer readout (final value · change from the "
        "previous layer); Compare shows the baseline's layers too.",
        ["Lower the decoder LR / add warm-up for the head", "Check the reference-point refinement (detached between "
         "layers?)", "Check the layer index order (is 0 the first or the last layer?)", "Fewer decoder layers if the "
         "last ones only add noise"],
        "Each decoder layer should refine the previous one's boxes; a worse last layer means inference uses a "
        "degraded prediction.",
    )  # fmt: skip


def _q_layers_saturate(series: Series, _total: str | None) -> Answer:
    fams = _layer_families(series)
    q = "Do the later decoder layers still help?"
    if not fams:
        return Answer("layers_saturate", "layers", q, "na", "No per-layer losses logged.")
    notes: list[str] = []
    for fam, layers in fams.items():
        ends = [_end(series[k]) for _, k in layers]
        gains = [(ends[i - 1] - ends[i]) / (abs(ends[i - 1]) or 1e-12) for i in range(1, len(ends))]
        half = gains[len(gains) // 2 :]
        if len(gains) >= 3 and all(abs(g) < 0.01 for g in half):
            notes.append(f"{fam}: layers {layers[len(gains) // 2 + 1][0]}–{layers[-1][0]} change < 1% each")
    return Answer(
        "layers_saturate", "layers", q, "warn" if notes else "ok",
        "; ".join(notes[:4]) if notes else "Every layer still improves on the previous one by ≥ 1% somewhere in the "
        "second half.",
        [k for _, k in next(iter(fams.values()))][:12],
        "Metrics → Slice breakdowns → per-layer readout; compare a run with fewer layers or extra cross-attention "
        "inputs.",
        ["Fewer decoder layers (faster, same quality)", "Give later layers new information (another modality's tokens, "
         "temporal memory) instead of repeating the same cross-attention", "Check that later layers get their own "
         "auxiliary loss"],
        "Saturated layers cost compute and latency without improving boxes.",
    )  # fmt: skip


def _q_weighting(series: Series, total: str | None) -> Answer:
    sig = [k for k in series if re.search(r"sigma|log_?var|uncert", k, re.I)]
    q = "Does the loss weighting mute a task?"
    if not sig:
        return Answer("weighting", "weighting", q, "na", "No learned weights (σ / log variance) logged — fixed weights "
                      "or not logged; the Uncertainty tab reads them when they are.")  # fmt: skip
    grown = []
    for k in sig:
        a, b = _at(series[k], 0.0), _at(series[k], 1.0)
        if math.isfinite(a) and math.isfinite(b) and b - a > 0.5:  # log σ up by 0.5 ⇒ weight × e^-1
            grown.append(f"{k} {_num(a)}→{_num(b)}")
    neg = total is not None and _end(series[total]) < 0
    st = "warn" if grown or neg else "ok"
    text = f"σ grew (its task's weight fell ≥ 2.7×): {', '.join(grown[:4])}." if grown else "No σ grew strongly."
    if neg:
        text += f" The logged total {total} is negative — it includes +log σ; compare unweighted terms between runs."
    return Answer(
        "weighting", "weighting", q, st, text, sig[:8],
        "Uncertainty tab (weight shares over time, balance, neglected head); Compare → Uncertainty.",
        ["Clamp σ or initialise it per task", "Fixed weights for a head that must not be muted", "Check that the "
         "regulariser (+log σ) is in the loss"],
        "Uncertainty weighting lowers the weight of hard or noisy tasks — sometimes the task you care about most.",
    )  # fmt: skip


def _q_grad(series: Series, _total: str | None) -> Answer:
    g = [k for k in series if re.search(r"grad_?norm", k, re.I) and "[" not in k]
    q = "Are the gradients exploding or clipped all the time?"
    if not g:
        return Answer("grad", "optimisation", q, "na", "No gradient norm logged (grad_norm).")
    k = g[0]
    _, v = _fin(series[k])
    if v.size < 8:
        return Answer("grad", "optimisation", q, "na", f"{k} has too few points.")
    early, late = float(np.median(v[: max(3, v.size // 5)])), float(np.median(signal.tail(v, 0.2)))
    ratio = late / (early or 1e-12)
    sp = signal.detect_spikes(*_fin(series[k]), z=8.0, min_rel_jump=1.0)
    st = "bad" if ratio > 5 else "warn" if ratio > 2 or len(sp) >= 3 else "ok"
    return Answer(
        "grad", "optimisation", q, st,
        f"{k}: late / early median {ratio:.2f}×, {len(sp)} spikes.", [k],
        "Metrics → gradients set (per module when logged); with the LR chart.",
        ["Gradient clipping at a value near the healthy median", "Lower LR", "Find the module: per-module grad norms "
         "(gradients set)"],
        "Growing gradients precede divergence; constant clipping means the LR is effectively lower than configured.",
    )  # fmt: skip


def _q_features(series: Series, _total: str | None) -> Answer:
    zero = [k for k in series if re.search(r"zero_?frac", k, re.I)]
    dead = [k for k in series if re.search(r"dead", k, re.I)]
    q = "Is a module or modality dying (features → 0, dead gradients)?"
    if not zero and not dead:
        return Answer("features", "features", q, "na", "No feature / gradient health series logged (feat_zero_frac, "
                      "grad_dead_frac).")  # fmt: skip
    hits = []
    for k in zero:
        a, b = _at(series[k], 0.0), _at(series[k], 1.0)
        if b > 0.9 or b - a > 0.3:
            hits.append(f"{k} {a:.2f}→{b:.2f}")
    for k in dead:
        if _at(series[k], 1.0) > 0.5:
            hits.append(f"{k} ends at {_at(series[k], 1.0):.2f}")
    return Answer(
        "features", "features", q, "bad" if hits else "ok",
        ("; ".join(hits[:5]) + ".") if hits else "No module's features or gradients die.",
        [h.split(" ")[0] for h in hits][:8] or (zero + dead)[:8],
        "Metrics → features / gradients sets; filter by modality; Slice breakdowns per module and scale.",
        ["Is the modality dropped by design (ablation, modality dropout)? — check the knowledge", "Normalisation / "
         "activation of that branch (dead ReLU)", "LR multiplier of that branch; pre-trained weights loaded?"],
        "A modality whose features go to zero contributes nothing — the model silently became single-sensor.",
    )  # fmt: skip


def _q_queries(series: Series, _total: str | None) -> Answer:
    pos = next((k for k in series if "positive_query_ratio" in k and "[" not in k), None)
    dup = next((k for k in series if "duplicate_rate" in k and "[" not in k), None)
    q = "Are the queries used (matched) and not duplicated?"
    if not pos and not dup:
        return Answer(
            "queries", "queries", q, "na", "No query statistics logged (positive_query_ratio, duplicate_rate)."
        )
    notes, st = [], "ok"
    if pos and _end(series[pos]) < 0.005:
        notes.append(f"only {_end(series[pos]):.2%} of queries matched")
        st = "warn"
    if dup:
        d0, d1 = _at(series[dup], 0.5), _at(series[dup], 1.0)
        if d1 > 0.1 or d1 > d0 * 1.2:
            notes.append(f"duplicate rate {d0:.3f}→{d1:.3f}")
            st = "warn"
    return Answer(
        "queries", "queries", q, st, ("; ".join(notes) + ".") if notes else "Queries are matched and duplicates fall.",
        [k for k in (pos, dup) if k],
        "Metrics → search 'query' / 'duplicate'; Findings → Sparse detection health.",
        ["Fewer queries or better initialisation (anchors from the previous frame / proposals)", "Duplicates: check "
         "self-attention between queries and the propagated memory; NMS-free decoders need it", "One-to-many matching "
         "early in training"],
        "Idle or duplicate queries waste capacity; rising duplicates hurt precision without NMS.",
    )  # fmt: skip


def _q_vs_baseline(series: Series, total: str | None, base: Series) -> list[Answer]:
    out: list[Answer] = []
    if total and total in base:
        c25, b25, c, b = _at(series[total], 0.25), _at(base[total], 0.25), _end(series[total]), _end(base[total])
        rel = c / (b or 1e-12) - 1
        lost = c25 < b25 and c > b
        st = "bad" if lost or rel > 0.05 else "warn" if rel > 0.01 else "ok"
        text = f"{total}: {_num(c)} vs baseline {_num(b)} ({_pct(rel)})."
        if lost:
            text += " Lower early, higher at the end — the early advantage is lost."
        out.append(Answer(
            "base_total", "baseline", "Is the candidate's loss better than the baseline's — and does it stay better?",
            st, text, [total], "Compare → Metrics (baseline dashed); zoom where the curves cross.",
            ["If the weights differ, compare the unweighted terms instead of the total", "Early advantage lost: the "
             "change helps optimisation early but not the optimum — try a longer schedule or a lower final LR"],
            "Totals are only comparable when the loss definition and weights are the same.",
        ))  # fmt: skip
    worse: list[tuple[str, float]] = []
    better: list[tuple[str, float]] = []
    for k in [*_terms(series, total), *[k for f in _layer_families(series).values() for _, k in f]]:
        if k in base:
            rel = _end(series[k]) / (_end(base[k]) or 1e-12) - 1
            (worse if rel > 0.02 else better if rel < -0.02 else []).append((k, rel))
    if worse or better:
        worse.sort(key=lambda x: -x[1])
        better.sort(key=lambda x: x[1])
        text = ("Worse: " + ", ".join(f"{k} {_pct(r)}" for k, r in worse[:5]) + ". " if worse else "") + (
            "Better: " + ", ".join(f"{k} {_pct(r)}" for k, r in better[:5]) + "." if better else ""
        )
        out.append(Answer(
            "base_terms", "baseline", "Which loss terms and layers got worse than the baseline?",
            "warn" if worse else "ok", text.strip(), [k for k, _ in worse[:6]] or [k for k, _ in better[:6]],
            "Compare → Deltas & config (filter the losses); Compare → Metrics, per term.",
            ["Trace each worse term to a config difference (Deltas & config → meaningful changes)", "A worse auxiliary "
             "layer with a better last layer is fine; a worse last layer is not"],
            "The total hides trade-offs between terms; the change usually explains one of them.",
        ))  # fmt: skip
    evalk = next((k for k in ("val/NDS", "val/mAP", "val/AMOTA", "val/AP") if k in series and k in base), None)
    if total and total in base and evalk:
        loss_better = _end(series[total]) < _end(base[total])
        eval_better = _end(series[evalk]) > _end(base[evalk])
        if loss_better != eval_better:
            out.append(Answer(
                "loss_vs_eval", "baseline", "Does the lower loss translate into better evaluation?", "warn",
                f"{total} is {'lower' if loss_better else 'higher'} than the baseline but {evalk} is "
                f"{'worse' if loss_better else 'better'}.", [total, evalk],
                "Compare → Deltas & config (evaluation metrics) next to the loss curves.",
                ["Overfitting (check val loss)", "The loss optimises something the metric does not reward (matching "
                 "vs evaluation thresholds, classes / ranges the metric weights) — harness task metric_definitions",
                 "Evaluation noise: replicate the baseline (seeds)"],
                "Loss and evaluation metric disagree — the change helps the objective but not the goal, or the other "
                "way round.",
            ))  # fmt: skip
    return out


QUESTIONS: tuple[Callable[[Series, str | None], Answer], ...] = (
    _q_diverge, _q_nonfinite, _q_spikes, _q_convergence, _q_plateau, _q_oscillation, _q_overfit,
    _q_term_rising, _q_term_dominates, _q_term_flat,
    _q_layers_diverge, _q_layers_saturate,
    _q_weighting, _q_grad, _q_features, _q_queries,
)  # fmt: skip


def ask(series: Series, baseline: Series | None = None) -> list[Answer]:
    """Every question answered for ``series`` (and against ``baseline`` when given). Never raises for odd data."""
    total = _total(series)
    out: list[Answer] = []
    for q in QUESTIONS:
        try:
            out.append(q(series, total))
        except Exception as e:  # one odd series must not hide the other answers
            out.append(Answer(q.__name__.removeprefix("_q_"), "trajectory", q.__name__, "na", f"could not answer: {e}"))
    if baseline:
        out += _q_vs_baseline(series, total, baseline)
    return out
