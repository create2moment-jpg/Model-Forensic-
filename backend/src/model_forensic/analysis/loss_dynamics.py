"""Loss dynamics: what the learning-rate schedule did vs what the model learned (A1), and how fast each loss term
trains relative to the others (A2). See docs/guide/learning-from-the-loss.md.

**A1 — schedule-aware loss model** (Tissue et al., 2024, *Scaling Law with Learning Rate Annealing*):

    L(s) ≈ L0 + A · S1(s)^(-α) − C · S2(s)

S1 is the forward area (summed LR up to step s, in units of steps at the peak LR); S2 the annealing area
(``m_i = λ·m_{i-1} + (η_{i-1} − η_i)``, ``S2 = Σ m_i`` — LR drops, remembered with decay λ per step). Fitted on the
logged loss after warm-up, the model separates the part of a loss drop the schedule explains (``C·S2``) from the
part driven by learning (``A·S1^-α``), predicts the loss under another schedule (longer, decayed later), and puts
two runs on *one* schedule: the candidate's fitted curve evaluated on the baseline's schedule shows how much of a
gap remains once the schedule is equal. Derived for language-model pretraining at a fixed batch size: every fit
reports its R² and nothing is read from a fit below ``loss_dynamics.fit_min_r2``.

**A2 — training rate per loss term** (GradNorm's loss ratio, Chen et al., 2018): ``L̃_i(t) = L_i(t) / L_i(0)``
and the relative inverse training rate ``r_i(t) = L̃_i(t) / mean_j L̃_j(t)`` within a group (the loss terms; each
family of per-layer / per-branch losses). ``r > 1`` trains slower than its group. A diagnosis, not a recipe:
Kurin et al. (2022) show that a tuned plain sum of losses matches adaptive re-weighting.

Deterministic, no I/O. Every number is a reading with its fit quality, never a fact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from model_forensic.analysis import signal, thresholds
from model_forensic.analysis.series_ops import is_loss, is_val, loss_terms, parse_key, total_loss
from model_forensic.domain.metrics import MetricSeries

Series = Mapping[str, MetricSeries]
Arr = npt.NDArray[np.float64]

_ALPHAS = np.geomspace(0.02, 2.0, 80)
_GRID = 4000  # points of the LR grid (areas are integrated on it)
_BINS = 120  # the loss is binned (median) before fitting: noise of single steps does not drive the fit


def _t(key: str) -> float:
    return thresholds.t("loss_dynamics", key)


# ── A1: areas under the schedule ───────────────────────────────────────────────────────────────────────────────────
@dataclass
class Schedule:
    """The LR schedule on a uniform step grid with its forward (S1) and annealing (S2) areas."""

    steps: Arr
    lr: Arr  # normalised by the peak
    s1: Arr
    s2: Arr
    peak: float
    warmup_end: float  # first step at which the LR reaches 95% of its peak

    def at(self, steps: Arr) -> tuple[Arr, Arr]:
        return np.interp(steps, self.steps, self.s1), np.interp(steps, self.steps, self.s2)

    @property
    def end(self) -> float:
        return float(self.steps[-1])


def schedule(lr: MetricSeries, until: float | None = None, decay: float | None = None) -> Schedule | None:
    """Areas of the LR schedule from step 0 to ``until`` (default: the LR's last step; held constant after it)."""
    st, v = lr.finite()
    if v.size < 2 or float(np.nanmax(v)) <= 0:
        return None
    lam = _t("anneal_decay") if decay is None else decay
    end = float(until if until is not None else st[-1])
    if end <= 0:
        return None
    n = int(min(_GRID, max(2, end + 1)))
    grid = np.linspace(0.0, end, n)
    peak = float(np.nanmax(v))
    eta = np.interp(grid, st, v / peak)
    w = end / (n - 1)
    s1 = np.concatenate([[0.0], np.cumsum((eta[1:] + eta[:-1]) / 2 * w)])
    s2 = np.zeros(n)
    m = 0.0
    keep = lam**w
    for j in range(1, n):
        m = m * keep + max(0.0, float(eta[j - 1] - eta[j]))
        s2[j] = s2[j - 1] + m * w
    above = np.nonzero(eta >= 0.95)[0]
    return Schedule(grid, eta, s1, s2, peak, float(grid[above[0]]) if above.size else 0.0)


def stretched(lr: MetricSeries, factor: float) -> MetricSeries:
    """The same schedule shape, ``factor`` times as long."""
    st, v = lr.finite()
    return lr.model_copy(update={"steps": tuple(float(x) * factor for x in st), "values": tuple(float(x) for x in v)})


# ── A1: the fit ────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class ScheduleFit:
    key: str
    l0: float
    a: float
    alpha: float
    c: float
    r2: float
    points: int
    first_step: float
    last_step: float
    s1_range: tuple[float, float]
    end_fitted: float
    end_observed: float
    anneal_part: float  # C·S2 at the end: loss the LR decay took off
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.r2 >= _t("fit_min_r2")

    def predict(self, s1: Arr, s2: Arr) -> Arr:
        return self.l0 + self.a * np.power(np.maximum(s1, 1e-9), -self.alpha) - self.c * s2

    def view(self) -> dict[str, Any]:
        d = asdict(self)
        d["usable"] = self.usable
        return d


def _binned(st: Arr, v: Arr, lo: float) -> tuple[Arr, Arr]:
    keep = st >= lo
    st, v = st[keep], v[keep]
    if st.size <= _BINS:
        return st, v
    edges = np.linspace(st[0], st[-1], _BINS + 1)
    idx = np.clip(np.searchsorted(edges, st, side="right") - 1, 0, _BINS - 1)
    xs, ys = [], []
    for b in range(_BINS):
        sel = idx == b
        if sel.any():
            xs.append(float(np.mean(st[sel])))
            ys.append(float(np.median(v[sel])))
    return np.asarray(xs), np.asarray(ys)


def _solve(y: Arr, cols: list[Arr]) -> tuple[Arr, float]:
    x = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return coef, float(np.sum((x @ coef - y) ** 2))


def fit(loss: MetricSeries, sched: Schedule, key: str = "") -> ScheduleFit | None:
    """Fit ``L0 + A·S1^-α − C·S2`` (A, C ≥ 0) on the loss after warm-up; None when there is too little to fit."""
    st, v = loss.finite()
    if v.size < 8:
        return None
    lo = max(sched.warmup_end, float(st[0]))
    xs, ys = _binned(st, v, lo)
    xs, ys = xs[xs <= sched.end], ys[xs <= sched.end]
    if xs.size < _t("min_points"):
        return None
    s1, s2 = sched.at(xs)
    ok = s1 > 0
    xs, ys, s1, s2 = xs[ok], ys[ok], s1[ok], s2[ok]
    if xs.size < _t("min_points"):
        return None
    best: tuple[float, float, float, float, float] | None = None  # sse, l0, a, alpha, c
    one = np.ones_like(ys)
    floor_free = bool(np.min(ys) <= 0)  # a positive loss has a floor ≥ 0 (else the fit trades L0 against α freely)
    for al in _ALPHAS:
        p = np.power(s1, -al)
        for use_l0, use_a, use_c in ((1, 1, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1), (0, 1, 0)):
            cols = ([one] if use_l0 else []) + ([p] if use_a else []) + ([-s2] if use_c else [])
            coef, sse = _solve(ys, cols)
            l0 = float(coef[0]) if use_l0 else 0.0
            a = float(coef[int(use_l0)]) if use_a else 0.0
            c = float(coef[-1]) if use_c else 0.0
            if a < 0 or c < 0 or (l0 < 0 and not floor_free):
                continue
            if best is None or sse < best[0]:
                best = (sse, l0, a, float(al), c)
    if best is None:
        return None
    sse, l0, a, alpha, c = best
    sst = float(np.sum((ys - ys.mean()) ** 2)) or 1e-12
    r2 = 1.0 - sse / sst
    e1, e2 = sched.at(np.asarray([xs[-1]]))
    end_fit = float(l0 + a * float(e1[0]) ** -alpha - c * float(e2[0]))
    notes = []
    if sched.end < 2000:
        notes.append(f"the schedule spans only {sched.end:g} steps — if steps are epochs, the annealing memory "
                     "(loss_dynamics.anneal_decay, per optimizer step) does not fit; read the fit with care")  # fmt: skip
    if _rises_after_peak(sched):
        notes.append("the LR rises again after its peak (restarts / cycles): the model assumes one decay")
    return ScheduleFit(
        key=key, l0=l0, a=a, alpha=alpha, c=c, r2=r2, points=int(xs.size), first_step=float(xs[0]),
        last_step=float(xs[-1]), s1_range=(float(np.min(s1)), float(np.max(s1))), end_fitted=end_fit,
        end_observed=float(ys[-1]), anneal_part=float(c * float(e2[0])), notes=notes,
    )  # fmt: skip


def _rises_after_peak(sched: Schedule) -> bool:
    i = int(np.argmax(sched.lr))
    tail = sched.lr[i:]
    return bool(tail.size > 2 and np.max(tail - np.minimum.accumulate(tail)) > 0.1)


def at_end(f: ScheduleFit, sched: Schedule) -> float:
    s1, s2 = sched.at(np.asarray([sched.end]))
    return float(f.predict(s1, s2)[0])


def longer(f: ScheduleFit, lr: MetricSeries, factor: float = 1.5) -> float | None:
    """Predicted end loss with the same schedule shape ``factor`` times as long (None if it does not fit)."""
    sch = schedule(stretched(lr, factor))
    return at_end(f, sch) if sch is not None else None


def _rel(a: float, b: float) -> float:
    return (a - b) / (abs(a) or 1e-12)


def _vs(g: float) -> str:
    """A relative gap (+ = candidate lower) in words."""
    return (
        "level with the baseline"
        if abs(g) < 5e-4
        else f"{abs(g):.1%} {'lower' if g > 0 else 'higher'} than the baseline"
    )


def one_run(loss: MetricSeries, lr: MetricSeries, key: str) -> dict[str, Any]:
    """The fit for one loss of one run, with what it says (or why it says nothing)."""
    st, _ = loss.finite()
    sch = schedule(lr, until=float(st[-1]) if st.size else None)
    f = fit(loss, sch, key) if sch is not None else None
    if f is None:
        return {"key": key, "fit": None, "sentence": f"{key}: too few points after warm-up to fit the LR model."}
    out: dict[str, Any] = {"key": key, "fit": f.view()}
    if not f.usable:
        out["sentence"] = (f"{key}: the LR model does not describe this curve (R² {f.r2:.2f} < "
                           f"{_t('fit_min_r2'):.2f}) — nothing is read from it.")  # fmt: skip
        return out
    share = f.anneal_part / (abs(f.end_fitted) + f.anneal_part or 1e-12)
    lg = longer(f, lr, 1.5)
    out["anneal_share"] = share
    out["longer_1_5x"] = lg
    decay = (
        f"The LR decay took {f.anneal_part:.4g} off the loss ({share:.0%} of where it would be without the decay)"
        if f.anneal_part > 1e-9
        else "No part of the drop is attributed to the LR decay"
    )
    no_floor = f.l0 <= 1e-12 < f.end_observed
    floor = (
        "no floor is visible yet (the fit puts it at 0: the loss is still far from where it settles)"
        if no_floor
        else f"the fitted floor is {f.l0:.4g}"
    )
    out["floor_identified"] = not no_floor
    out["sentence"] = (
        f"{key}: fits the LR model (R² {f.r2:.2f}). {decay}; {floor}"
        + (f"; 1.5× longer with the same shape would reach ≈ {lg:.4g} ({_rel(f.end_fitted, lg):.1%} lower"
           + ("; optimistic — the floor is not identified" if no_floor else "") + ")" if lg is not None else "") + "."
    )  # fmt: skip
    return out


def schedule_effect(
    base_loss: MetricSeries, base_lr: MetricSeries, cand_loss: MetricSeries, cand_lr: MetricSeries, key: str
) -> dict[str, Any]:
    """How much of the gap between two runs on one loss remains once both run the baseline's schedule."""
    bst, _ = base_loss.finite()
    cst, _ = cand_loss.finite()
    bs = schedule(base_lr, until=float(bst[-1]) if bst.size else None)
    cs = schedule(cand_lr, until=float(cst[-1]) if cst.size else None)
    fb = fit(base_loss, bs, key) if bs is not None else None
    fc = fit(cand_loss, cs, key) if cs is not None else None
    out: dict[str, Any] = {"key": key, "base_fit": fb.view() if fb else None, "cand_fit": fc.view() if fc else None,
                           "verdict": "no_fit"}  # fmt: skip
    if fb is None or fc is None or bs is None or cs is None or not (fb.usable and fc.usable):
        r2 = [f"{f.r2:.2f}" for f in (fb, fc) if f is not None]
        out["sentence"] = (f"{key}: the LR model does not fit both runs" + (f" (R² {', '.join(r2)})" if r2 else "")
                           + " — the schedule cannot be separated from the change.")  # fmt: skip
        return out
    b_end = at_end(fb, bs)
    c_end = at_end(fc, cs)
    c_on_b = at_end(fc, bs)
    observed = _rel(b_end, c_end)  # + = candidate lower (better)
    equal = _rel(b_end, c_on_b)
    out.update(observed_gap=observed, equal_schedule_gap=equal, base_end=b_end, cand_end=c_end, cand_on_base=c_on_b)
    same = abs(_rel(bs.s1[-1], cs.s1[-1])) < 0.01 and abs(bs.s2[-1] - cs.s2[-1]) <= 0.01 * (abs(bs.s2[-1]) or 1.0)
    extrap = bs.s1[-1] > 1.2 * fc.s1_range[1]
    out["extrapolated"] = bool(extrap)
    tiny = _t("gap_min")
    if same:
        out["verdict"] = "same_schedule"
        out["sentence"] = (f"{key}: both runs follow the same LR schedule — the candidate ends {_vs(observed)}; that is "
                           "the change itself, nothing to separate.")  # fmt: skip
        return out
    if abs(observed) < tiny and abs(equal) < tiny:
        out["verdict"] = "no_gap"
        out["sentence"] = (
            f"{key}: no gap on either schedule (observed: {_vs(observed)}; on equal schedules: {_vs(equal)})."
        )
        return out
    share = (observed - equal) / observed if abs(observed) >= tiny else math.nan
    out["schedule_share"] = share
    ext = " (extrapolated beyond the candidate's own schedule — less certain)" if extrap else ""
    if abs(observed) >= tiny and math.isfinite(share) and share >= _t("schedule_share") and abs(equal) < abs(observed):
        out["verdict"] = "mostly_schedule"
        out["sentence"] = (
            f"{key}: the candidate ends {_vs(observed)}, but on the baseline's schedule it would be {_vs(equal)}{ext}"
            f" — {min(share, 1.0):.0%} of the gap is the LR schedule, not the change."
        )
    elif equal * observed < 0 or abs(observed) < tiny:
        out["verdict"] = "masked"
        out["sentence"] = (
            f"{key}: the candidate ends {_vs(observed)}, but on the baseline's schedule it would be {_vs(equal)}{ext}"
            " — the schedule hides the change's real effect."
        )
    else:
        out["verdict"] = "holds"
        bigger = abs(equal) > 1.5 * abs(observed)
        out["sentence"] = (
            f"{key}: the gap holds on the baseline's schedule (observed: {_vs(observed)}; on equal schedules: "
            f"{_vs(equal)}{ext}) — it is the change, not the schedule"
            + (" — and the candidate's own schedule hides part of it." if bigger else ".")
        )
    return out


# ── A2: training rate per term ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Rate:
    key: str
    group: str
    start: float  # L(0): median of the first points
    ratio_end: float  # L̃ at the end
    rate_end: float  # r at the end (> 1: slower than the group)
    lag_from: float | None  # share of training from which r stays above the lag threshold
    status: str  # lagging | leading | normal | not_learning
    curve: list[float] = field(default_factory=list)  # r over 20 points of the schedule


_FRAC = np.linspace(0.0, 1.0, 50)


def _ratio_curve(s: MetricSeries) -> tuple[float, Arr] | None:
    st, v = s.finite()
    if v.size < 20 or float(np.min(v)) <= 0:
        return None
    k = max(3, v.size // 20)
    start = float(np.median(v[:k]))
    if start <= 0:
        return None
    sm = signal.ema(v, 0.2)
    span = float(st[-1] - st[0]) or 1.0
    return start, np.interp(_FRAC, (st - st[0]) / span, sm) / start


def groups(series: Series) -> dict[str, list[str]]:
    """The loss terms; and each family of losses that differ by one dimension (layer, branch, module …)."""
    out: dict[str, list[str]] = {}
    terms = [k for k in loss_terms(series, total_loss(series)) if not _aux(k)]
    if len(terms) >= 2:
        out["terms"] = terms
    for k in series:
        if not is_loss(k) or is_val(k):
            continue
        base, dims = parse_key(k)
        if len(dims) == 1:
            out.setdefault(f"{base}[{next(iter(dims))}=*]", []).append(k)
    return {g: sorted(v) for g, v in out.items() if len(v) >= 2}


def _aux(k: str) -> bool:
    return any(x in k.lower() for x in ("sigma", "log_var", "logvar", "uncert", "weight"))


def training_rates(series: Series) -> list[Rate]:
    lag, lead = _t("rate_lag"), _t("rate_lead")
    out: list[Rate] = []
    for g, keys in groups(series).items():
        curves = {k: c for k in keys if (c := _ratio_curve(series[k])) is not None}
        if len(curves) < 2:
            continue
        mean = np.mean(np.stack([cv for _, cv in curves.values()]), axis=0)
        for k, (start, ratio) in curves.items():
            r = ratio / np.where(mean > 0, mean, np.nan)
            r_end, ratio_end = float(r[-1]), float(ratio[-1])
            above = r > lag
            lag_from = None
            if r_end > lag:
                j = len(r) - 1
                while j > 0 and above[j - 1]:
                    j -= 1
                lag_from = float(_FRAC[j])
            status = ("not_learning" if ratio_end >= 1 - _t("flat_drop") else "lagging" if r_end > lag
                      else "leading" if r_end < lead else "normal")  # fmt: skip
            out.append(Rate(k, g, start, ratio_end, r_end, lag_from, status,
                            [round(float(x), 4) for x in r[:: max(1, len(r) // 20)]]))  # fmt: skip
    return out


def rate_sentence(r: Rate) -> str:
    if r.status == "not_learning":
        return (f"{r.key} does not fall ({r.ratio_end:.0%} of its start) while its group ({r.group}) trains — it "
                "learns nothing here")  # fmt: skip
    if r.status == "lagging":
        return (f"{r.key} trains {r.rate_end:.2f}× slower than its group ({r.group})"
                + (f" from {r.lag_from:.0%} of training" if r.lag_from is not None else "")
                + f"; it is at {r.ratio_end:.0%} of its start")  # fmt: skip
    if r.status == "leading":
        return (
            f"{r.key} trains faster than its group ({r.group}): r {r.rate_end:.2f}, at {r.ratio_end:.0%} of its start"
        )
    return f"{r.key}: r {r.rate_end:.2f} in {r.group}"


# ── report ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _fit_keys(series: Series, limit: int) -> list[str]:
    total = total_loss(series)
    keys = [total] if total else []
    return keys + [k for k in loss_terms(series, total) if not _aux(k)][: max(0, limit - len(keys))]


def report(
    series: Series,
    lr: MetricSeries | None,
    baseline: Series | None = None,
    base_lr: MetricSeries | None = None,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    """Both readings for a run (and against a baseline): schedule fits / effects, training rates, sentences."""
    rates = training_rates(series)
    base_rates = {r.key: r for r in training_rates(baseline)} if baseline else {}
    readings: list[str] = []
    out: dict[str, Any] = {"lr_logged": lr is not None, "fits": [], "effects": []}
    if lr is None:
        readings.append("No learning-rate series: the schedule cannot be separated from learning (log train/lr or "
                        "keep Lightning's metrics.csv).")  # fmt: skip
    else:
        for k in _fit_keys(series, limit):
            out["fits"].append(one_run(series[k], lr, k))
        if baseline is not None and base_lr is not None:
            for k in [k for k in _fit_keys(series, limit) if k in baseline]:
                out["effects"].append(schedule_effect(baseline[k], base_lr, series[k], lr, k))
        elif baseline is not None:
            readings.append("The baseline has no learning-rate series: runs cannot be put on one schedule.")
        sched = [e for e in out["effects"] if e["verdict"] == "mostly_schedule"]
        if sched:
            readings.append(f"{len(sched)} loss gap(s) are mostly the LR schedule, not the change: "
                            + "; ".join(e["sentence"] for e in sched[:3]))  # fmt: skip
        held = [e for e in out["effects"] if e["verdict"] == "holds"]
        if held:
            readings.append(f"{len(held)} loss gap(s) hold on equal schedules — the change itself: "
                            + ", ".join(e["key"] for e in held[:5]) + ".")  # fmt: skip
        anneal = [f for f in out["fits"] if f.get("anneal_share") is not None and f["anneal_share"] >= 0.3]
        if anneal:
            readings.append("Most of the late drop comes from the LR decay for " + ", ".join(f["key"] for f in anneal[:4])
                            + ": comparisons before the decay say little, and a run cut short compares unfairly.")  # fmt: skip
    slow = [r for r in rates if r.status in ("lagging", "not_learning")]
    for r in slow[:6]:
        b = base_rates.get(r.key)
        readings.append(rate_sentence(r) + (f" (baseline: r {b.rate_end:.2f})" if b else "") + ".")
    out["rates"] = [
        {**asdict(r), "base_rate_end": base_rates[r.key].rate_end if r.key in base_rates else None} for r in rates
    ]
    out["readings"] = readings
    return out


def problems(rep: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The readings that need attention, as plain dicts (the application turns them into Observations)."""
    out: list[dict[str, Any]] = []
    for e in rep.get("effects", []):
        if e["verdict"] in ("mostly_schedule", "masked"):
            out.append({"id": f"schedule:{e['key']}", "status": "warn", "key": e["key"], "text": e["sentence"],
                        "title": "Is the gap only the LR schedule?"})  # fmt: skip
        elif e["verdict"] == "holds":
            out.append({"id": f"schedule:{e['key']}", "status": "info", "key": e["key"], "text": e["sentence"],
                        "title": "Is the gap only the LR schedule?"})  # fmt: skip
    for f in rep.get("fits", []):
        if f.get("fit") and f["fit"]["usable"]:
            out.append({"id": f"fit:{f['key']}", "status": "info", "key": f["key"], "text": f["sentence"],
                        "title": "What did the LR schedule do?"})  # fmt: skip
    return out


def keys_of(rates: Sequence[Rate], status: Sequence[str]) -> list[str]:
    return [r.key for r in rates if r.status in status]
