"""The baseline study: how each loss family progressed, which losses the uncertainty weighting (σ) trusts or mutes,
and whether the composed configuration agrees with what the run actually logged. Deterministic, no rendering.

Conventions (Kendall-style uncertainty weighting as logged by the training code): ``uloss/<term>`` is the raw loss,
``loss_noreg/<term>`` the part with gradient to the network (``exp(-2s)·raw``), ``loss/<term>`` adds the ``s`` tax,
``task_log_sigma[term=…]`` is ``s = log σ``. The effective weight of a term is ``exp(-2s)``.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.domain.naming import loss_family

BINS = 100
FloatArray = npt.NDArray[np.float64]


def binned(s: MetricSeries, bins: int = BINS) -> FloatArray:
    """Mean value per 1 % of training (by step), gaps interpolated — a smooth, comparable curve."""
    x, v = s.finite()
    out = np.full(bins, np.nan)
    if v.size < 2 or x[-1] <= x[0]:
        return out
    f = (x - x[0]) / (x[-1] - x[0])
    idx = np.minimum((f * bins).astype(int), bins - 1)
    sums = np.bincount(idx, weights=v, minlength=bins)
    cnt = np.bincount(idx, minlength=bins)
    have = cnt > 0
    out[have] = sums[have] / cnt[have]
    grid = np.arange(bins, dtype=float)
    if have.sum() >= 2:
        out = np.interp(grid, grid[have], out[have])
    return out


def _train_terms(series: Mapping[MetricKey, MetricSeries], prefix: str) -> dict[tuple[str, Any], MetricSeries]:
    out = {}
    for k, s in series.items():
        if k.split == Split.TRAIN and "stage" not in k.dim_dict and k.name.startswith(prefix + "/"):
            out[(k.name[len(prefix) + 1 :], k.dims)] = s
    return out


def term_label(term: str, dims: Any) -> str:
    extra = ",".join(f"{a}={b}" for a, b in dims if a not in {"task", "term"})
    return f"{term}[{extra}]" if extra else term


# ------------------------------------------------------------------------------------------- loss progress
@dataclass
class FamilyProgress:
    family: str
    terms: int
    start: float
    end: float
    change: float  # relative change start → end (negative = fell)
    t50: float | None  # fraction of training at which half of the total drop was reached
    t90: float | None
    late_change: float  # relative change over the last 20 % of training (vs the start value)
    verdict: str  # still learning | plateaued | rising | flat
    curve: list[float] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)


def _reach(curve: FloatArray, start: float, end: float, frac: float) -> float | None:
    drop = start - end
    if not math.isfinite(drop) or drop <= 0:
        return None
    target = start - frac * drop
    hit = np.nonzero(curve <= target)[0]
    return float(hit[0] + 1) / len(curve) if hit.size else None


def _progress(family: str, curve: FloatArray, n: int, keys: list[str]) -> FamilyProgress:
    head = float(np.nanmean(curve[:5]))
    tail = float(np.nanmean(curve[-5:]))
    scale = abs(head) or 1.0
    change = (tail - head) / scale
    late = (float(np.nanmean(curve[-3:])) - float(np.nanmean(curve[78:82]))) / scale
    if abs(change) < 0.02 and abs(late) < 0.01:
        verdict = "flat"
    elif change > 0.02:
        verdict = "rising"
    elif late < -0.03:
        verdict = "still learning"
    else:
        verdict = "plateaued"
    return FamilyProgress(
        family,
        n,
        head,
        tail,
        change,
        _reach(curve, head, tail, 0.5),
        _reach(curve, head, tail, 0.9),
        late,
        verdict,
        [round(float(v), 6) for v in curve],
        keys,
    )


def loss_progress(series: Mapping[MetricKey, MetricSeries], slot: str = "uloss") -> list[FamilyProgress]:
    """Per loss family (summed over its terms): start, end, when the drop happened, and whether it still moves.
    ``slot`` is ``uloss`` (raw: what the network achieves, independent of σ — the default), ``loss_noreg`` (what
    reaches the gradient; moves with σ) or ``loss`` (weighted)."""
    terms = _train_terms(series, slot) or _train_terms(series, "loss_noreg")
    fams: dict[str, list[tuple[FloatArray, str]]] = defaultdict(list)
    for (term, dims), s in terms.items():
        c = binned(s)
        if np.isfinite(c).any():
            fams[loss_family(term, dims)].append((c, str(s.key)))
    out = [
        _progress(f, np.nansum(np.vstack([c for c, _ in items]), axis=0), len(items), [k for _, k in items])
        for f, items in fams.items()
    ]
    return sorted(out, key=lambda p: -abs(p.end))


def total_progress(series: Mapping[MetricKey, MetricSeries]) -> dict[str, FamilyProgress]:
    """The run totals: raw (``uloss``), network-facing (``loss_noreg``) and weighted (``loss``)."""
    out: dict[str, FamilyProgress] = {}
    for name in ("uloss", "loss_noreg", "loss"):
        for k, s in series.items():
            if k.name == name and k.split == Split.TRAIN and not k.dims:
                c = binned(s)
                if np.isfinite(c).any():
                    out[name] = _progress(name, c, 1, [str(k)])
    return out


# ------------------------------------------------------------------------------------- uncertainty weights
@dataclass
class TermWeight:
    term: str
    family: str
    key: str
    s_start: float
    s_end: float
    w_start: float  # exp(-2 s) at the start
    w_end: float
    rel_to_median: float  # w_end / median w_end over all terms
    trend: str  # losing trust | gaining trust | stable
    verdict: str  # self-muted | down-weighted | up-weighted | neutral
    raw_end: float | None = None
    grad_share: float | None = None  # share of the network-facing loss at the end
    curve: list[float] = field(default_factory=list)  # exp(-2 s) per 1 % of training


def uncertainty_weights(series: Mapping[MetricKey, MetricSeries]) -> list[TermWeight]:
    """Which losses σ trusts (weights up) or mutes — per term, start vs end, sorted by end weight."""
    rows: list[TermWeight] = []
    raws = {term_label(t, d): s for (t, d), s in _train_terms(series, "uloss").items()}
    noreg = {term_label(t, d): s for (t, d), s in _train_terms(series, "loss_noreg").items()}
    noreg_end = {k: float(np.nanmean(binned(s)[-5:])) for k, s in noreg.items()}
    tot = sum(abs(v) for v in noreg_end.values() if math.isfinite(v)) or 1.0
    for k, s in series.items():
        if k.name != "task_log_sigma" or k.split != Split.TRAIN or "stage" in k.dim_dict:
            continue
        c = binned(s)
        if not np.isfinite(c).any():
            continue
        d = k.dim_dict
        term = d.get("term", "")
        lab = term_label(term, k.dims)
        s0, s1 = float(np.nanmean(c[:5])), float(np.nanmean(c[-5:]))
        ds = s1 - s0
        raw = raws.get(lab)
        rows.append(
            TermWeight(
                lab,
                loss_family(term, tuple((a, b) for a, b in k.dims if a != "term")),
                str(k),
                s0,
                s1,
                math.exp(-2 * s0),
                math.exp(-2 * s1),
                math.nan,
                "losing trust" if ds > 0.1 else "gaining trust" if ds < -0.1 else "stable",
                "neutral",
                float(np.nanmean(binned(raw)[-5:])) if raw is not None else None,
                abs(noreg_end[lab]) / tot if lab in noreg_end and math.isfinite(noreg_end[lab]) else None,
                [round(math.exp(-2 * float(v)), 6) for v in c],
            )
        )
    if not rows:
        return rows
    med = float(np.median([r.w_end for r in rows])) or 1.0
    for r in rows:
        r.rel_to_median = r.w_end / med
        r.verdict = (
            "self-muted"
            if r.rel_to_median < 0.1
            else "down-weighted"
            if r.rel_to_median < 0.5
            else "up-weighted"
            if r.rel_to_median > 2
            else "neutral"
        )
    return sorted(rows, key=lambda r: r.w_end)


def family_weight_curves(rows: Sequence[TermWeight]) -> dict[str, list[float]]:
    """Mean effective weight per family over training (for one readable chart)."""
    by: dict[str, list[list[float]]] = defaultdict(list)
    for r in rows:
        by[r.family].append(r.curve)
    return {f: [float(x) for x in np.nanmean(np.array(cs, dtype=float), axis=0)] for f, cs in by.items()}


# ----------------------------------------------------------------------------------- config vs behaviour
_SRC = re.compile(r"^(?:(?P<stage>[^/.]+)/)?(?P<src>.*?\.(?:ya?ml|json)\.)?(?P<key>.+)$")


def effective_config(flat: Mapping[str, Any], stages: Sequence[str] = ()) -> dict[str, Any]:
    """One namespace of the configuration that ran: source prefixes stripped; for a pipeline the (first) training
    stage's values; the composed config wins over the submitted one."""
    st = set(stages)
    groups: dict[str, list[tuple[int, str, Any]]] = defaultdict(list)
    for k, v in flat.items():
        stage, rest = "", k
        head = k.split("/", 1)[0]
        if "/" in k and head in st:
            stage, rest = head, k.split("/", 1)[1]
        m = _SRC.match(rest)
        src, key = (m.group("src") or "", m.group("key")) if m else ("", rest)
        prio = 2 if "composed" in src else 1 if src else 0
        groups[stage].append((prio, key, v))
    if not groups:
        return {}
    pick = next((s for s in stages if "train" in s.lower() and s in groups), "" if "" in groups else next(iter(groups)))
    out: dict[str, Any] = {}
    for _prio, key, v in sorted(groups[pick], key=lambda t: t[0]):
        out[key] = v
    return out


@dataclass
class ConfigReading:
    topic: str
    config: str
    observed: str
    verdict: str  # consistent | mismatch | unverifiable | note
    detail: str = ""


def _vals(cfg: Mapping[str, Any], *suffixes: str) -> dict[str, Any]:
    return {k: v for k, v in cfg.items() if any(k == s or k.endswith("." + s) for s in suffixes)}


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in {"true", "false", "yes", "no", "on", "off"}:
        return v.lower() in {"true", "yes", "on"}
    n = _num(v)
    return None if n is None else n != 0


TOKEN_ALIASES = {
    "box": ("box", "bbox"),
    "bbox": ("box", "bbox"),
    "reg": ("box", "bbox", "center", "width", "l1"),
    "cls": ("cls", "class"),
    "class": ("cls", "class"),
    "vel": ("vel",),
    "velocity": ("vel",),
}


def config_vs_behaviour(
    cfg: Mapping[str, Any],
    series: Mapping[MetricKey, MetricSeries],
    weights: Sequence[TermWeight] = (),
    lr: MetricSeries | None = None,
) -> list[ConfigReading]:
    """What the composed config promises vs what the run logged."""
    out: list[ConfigReading] = []
    # 1. uncertainty weighting on/off vs σ actually moving
    unc = {k: _truthy(v) for k, v in cfg.items() if "uncertainty" in k.lower() and _truthy(v) is not None}
    unc = {k: v for k, v in unc.items() if not k.lower().endswith(("weight", "init"))} or unc
    moving = [w for w in weights if abs(w.s_end - w.s_start) > 0.05]
    if unc:
        on = any(unc.values())
        cfg_txt = ", ".join(f"{k} = {v}" for k, v in list(unc.items())[:3])
        if not weights:
            out.append(ConfigReading("Uncertainty weighting", cfg_txt, "no σ series logged", "unverifiable"))
        elif len(set(unc.values())) > 1:
            out.append(
                ConfigReading(
                    "Uncertainty weighting",
                    cfg_txt,
                    f"σ logged for {len(weights)} terms; {len(moving)} moved by > 0.05",
                    "mismatch",
                    "the config's switches disagree with each other; the logs say the weighting is "
                    + ("on" if moving else "off")
                    + " — find which switch the code reads",
                )
            )
        elif on:
            out.append(
                ConfigReading(
                    "Uncertainty weighting",
                    cfg_txt,
                    f"σ logged for {len(weights)} terms; {len(moving)} moved by > 0.05",
                    "consistent" if moving else "mismatch",
                    "" if moving else "enabled, but σ never moved — is it trainable (lr / frozen)?",
                )
            )
        else:
            out.append(
                ConfigReading(
                    "Uncertainty weighting",
                    cfg_txt,
                    f"σ logged for {len(weights)} terms; {len(moving)} moved",
                    "mismatch" if moving else "consistent",
                    "disabled in the config, yet σ moves — another switch enables it" if moving else "",
                )
            )
    # 2. configured task weights vs the gradient share each group actually receives
    tw: dict[str, float] = {}
    for k, v in _vals(cfg, *(f"task_weights.{t}" for t in TOKEN_ALIASES)).items():
        n = _num(v)
        if n is not None:
            tw[k.rsplit(".", 1)[-1]] = n
    have_share = [w for w in weights if w.grad_share is not None]
    if tw and have_share:
        tot_cfg = sum(abs(v) for v in tw.values()) or 1.0
        for tok, val in tw.items():
            al = TOKEN_ALIASES.get(tok, (tok,))
            got = sum(w.grad_share or 0.0 for w in have_share if any(a in w.term.split("[")[0].lower() for a in al))
            want = abs(val) / tot_cfg
            ratio = got / want if want else math.nan
            off = math.isfinite(ratio) and (ratio > 2 or ratio < 0.5)
            out.append(
                ConfigReading(
                    f"Task weight · {tok}",
                    f"task_weights.{tok} = {val:g} ({want:.0%} of the configured weight)",
                    f"{got:.0%} of the network-facing loss at the end",
                    "mismatch" if off else "consistent",
                    "σ and loss magnitude rebalanced it — the configured weight is not what the network feels"
                    if off
                    else "",
                )
            )
    # 3. schedule length vs what was logged
    total = next(
        (s for k, s in series.items() if k.name in {"loss", "uloss"} and k.split == Split.TRAIN and not k.dims), None
    )
    ms = {k: n for k, v in _vals(cfg, "max_steps", "total_steps", "num_steps").items() if (n := _num(v))}
    if ms and total is not None and len(total):
        last = float(total.steps[-1])
        want = max(ms.values())
        ok = 0.95 * want <= last <= 1.05 * want
        out.append(
            ConfigReading(
                "Schedule length",
                ", ".join(f"{k} = {v:g}" for k, v in ms.items()),
                f"training loss logged up to step {last:g}",
                "consistent" if ok else "mismatch",
                ""
                if ok
                else f"stopped at {last / want:.0%} of the configured steps — interrupted or resumed?"
                if last < want
                else f"ran {last / want:.1f}× the configured steps — another limit (epochs?) governs the schedule",
            )
        )
    # 4. learning rate
    lrs = {k: n for k, v in _vals(cfg, "lr", "learning_rate", "base_lr").items() if (n := _num(v))}
    if lrs and lr is not None and len(lr):
        _, v = lr.finite()
        if v.size:
            peak = float(np.max(v))
            want = max(lrs.values())
            ok = abs(peak - want) <= 0.1 * want
            out.append(
                ConfigReading(
                    "Learning rate",
                    ", ".join(f"{k} = {x:g}" for k, x in lrs.items()),
                    f"peak logged LR {peak:.3g}, final {float(v[-1]):.3g}",
                    "consistent" if ok else "note",
                    "" if ok else "the logged peak differs from the configured LR (scaling rule, per-group LR?)",
                )
            )
    # 5. decoder depth vs losses per layer
    nl = {k: n for k, v in _vals(cfg, "num_layers", "num_decoder_layers").items() if (n := _num(v))}
    layers = {
        int(d["layer"])
        for k in series
        if k.split == Split.TRAIN and (d := k.dim_dict).get("layer", "").isdigit() and d.get("branch") != "dn"
    }
    if nl and layers:
        want = int(max(nl.values()))
        ok = len(layers) == want
        out.append(
            ConfigReading(
                "Decoder depth",
                ", ".join(f"{k} = {int(x)}" for k, x in nl.items()),
                f"losses logged for {len(layers)} decoder layers",
                "consistent" if ok else "mismatch",
                "" if ok else "auxiliary losses do not cover every layer (or the config is not what ran)",
            )
        )
    return out


def sources_disagree(flat: Mapping[str, Any], stages: Sequence[str] = ()) -> list[tuple[str, list[str]]]:
    """Keys whose submitted and composed values differ inside the same stage (the run did not use what was asked)."""
    st = set(stages)
    seen: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for k, v in flat.items():
        stage, rest = "", k
        head = k.split("/", 1)[0]
        if "/" in k and head in st:
            stage, rest = head, k.split("/", 1)[1]
        m = _SRC.match(rest)
        if not m:
            continue
        seen[(stage, m.group("key"))][m.group("src") or "config"] = str(v)
    out = []
    for (stage, key), vals in seen.items():
        if len(set(vals.values())) > 1 and not any(p in key.lower() for p in ("output", "dir", "path", "seed")):
            out.append((f"{stage + '/' if stage else ''}{key}", [f"{s.rstrip('.')}: {v}" for s, v in vals.items()]))
    return sorted(out)[:30]
