"""Loss drift: how the candidate's losses *progress* differently from the baseline's — not only where they end.

For every loss both runs log (total, terms, per layer / branch / module), ``divergence.diverge`` finds the point
from which the curves stay apart beyond their own step-to-step noise and whether the gap holds, closes or crosses.
On top of that the *shape* of the progression is compared — how fast each converges (share of the schedule to reach
50% / 90% of its drop), how noisy, how many spikes — and the pattern across losses is read: losses drifting together
at one point of the schedule share a cause there; drift from the first steps fits a change of architecture / data /
loss definition; drift confined to one family points at that head or branch; gaps that close are about speed, not
the optimum.

When the schedules differ in length (> 10%), curves are aligned on the fraction of the schedule instead of the step.
Deterministic; the readings are hypotheses with their evidence, never facts.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from model_forensic.analysis import signal, thresholds
from model_forensic.analysis.divergence import diverge
from model_forensic.analysis.series_ops import is_loss as _is_loss
from model_forensic.analysis.series_ops import is_val as _is_val
from model_forensic.analysis.series_ops import noise as _noise
from model_forensic.analysis.series_ops import parse_key as _parse
from model_forensic.analysis.series_ops import reach as _reach
from model_forensic.domain.config import ConfigChange
from model_forensic.domain.metrics import MetricSeries

Series = Mapping[str, MetricSeries]


@dataclass
class DriftRow:
    key: str
    family: str
    outcome: str  # same | ahead | behind | closed | crossed | insufficient   (ahead = candidate lower loss)
    diverge_fraction: float | None
    final_gap: float | None  # + = candidate better (lower) at the end, relative
    max_gap: float | None
    threshold: float | None
    sentence: str
    shape: dict[str, Any] = field(default_factory=dict)
    shape_notes: list[str] = field(default_factory=list)
    lr_note: str = ""
    fits: list[str] = field(default_factory=list)
    score: float = 0.0


def _family(k: str) -> str:
    base, dims = _parse(k)
    mod = dims.get("module") or dims.get("modality") or dims.get("branch")
    return f"{base}[{mod}]" if mod else base


def _rescale(s: MetricSeries, span: float) -> MetricSeries:
    st, _ = s.arrays()
    top = float(np.nanmax(st)) if st.size else 0.0
    if top <= 0:
        return s
    return s.model_copy(update={"steps": tuple(float(x) * span / top for x in st)})


def _shape(ref: MetricSeries, cand: MetricSeries) -> tuple[dict[str, Any], list[str]]:
    r, c = _reach(ref), _reach(cand)
    rn, cn = _noise(ref.finite()[1]), _noise(cand.finite()[1])
    rsp = len(signal.detect_spikes(*ref.finite(), z=8.0, min_rel_jump=0.4))
    csp = len(signal.detect_spikes(*cand.finite(), z=8.0, min_rel_jump=0.4))
    shape = {"t50": [r["t50"], c["t50"]], "t90": [r["t90"], c["t90"]], "noise": [rn, cn], "spikes": [rsp, csp]}
    notes: list[str] = []
    if (
        r["t90"] is not None
        and c["t90"] is not None
        and abs(c["t90"] - r["t90"]) >= thresholds.t("loss_drift", "speed_diff")
    ):
        notes.append(
            f"converges {'slower' if c['t90'] > r['t90'] else 'faster'}: 90% of its drop at {c['t90']:.0%} of the "
            f"schedule vs {r['t90']:.0%}"
        )
    # noise relative to the loss level; below 0.5% both curves are smooth and a ratio says nothing
    if math.isfinite(rn) and math.isfinite(cn) and rn > 0 and max(rn, cn) > thresholds.t("loss_drift", "noise_min"):
        ratio = cn / rn
        nr = thresholds.t("loss_drift", "noise_ratio")
        if ratio > nr or ratio < 1 / nr:
            shown = f"×{min(ratio, 10):.1f}" + ("+" if ratio > 10 else "") if ratio > 1 else f"×{ratio:.2f}"
            notes.append(f"{'noisier' if ratio > 1 else 'smoother'}: {shown} the step-to-step noise")
    if csp - rsp >= 2 or (csp >= 2 and rsp == 0):
        notes.append(f"more spikes ({csp} vs {rsp})")
    return shape, notes


def loss_drift(
    ref: Series,
    cand: Series,
    *,
    ref_lr: MetricSeries | None = None,
    cand_lr: MetricSeries | None = None,
    changes: tuple[ConfigChange, ...] = (),
    limit: int = 300,
) -> dict[str, Any]:
    keys = sorted(k for k in ref if k in cand and _is_loss(k) and not _is_val(k))[:limit]
    keys += sorted(k for k in ref if k in cand and _is_loss(k) and _is_val(k))[: max(0, limit - len(keys))]
    if not keys:
        return {"rows": [], "aligned_by": "step", "readings": ["The runs share no loss series."], "summary": ""}
    r_top = max((float(np.nanmax(ref[k].arrays()[0])) for k in keys if len(ref[k])), default=0.0)
    c_top = max((float(np.nanmax(cand[k].arrays()[0])) for k in keys if len(cand[k])), default=0.0)
    by_fraction = r_top > 0 and c_top > 0 and abs(c_top / r_top - 1) > 0.1
    rows: list[DriftRow] = []
    for k in keys:
        a, b = ref[k], cand[k]
        if by_fraction:
            b = _rescale(b, r_top)
        d = diverge("candidate", a, b, ref_lr=ref_lr, cand_lr=_rescale(cand_lr, r_top) if (by_fraction and cand_lr) else cand_lr,
                    changes=changes)  # fmt: skip
        shape, notes = _shape(a, b)
        thr = d.threshold or 0.01
        mag = abs(d.max_gap or 0.0) / thr if d.outcome not in ("same", "insufficient") else 0.0
        early = 1.0 - (d.diverge_fraction if d.diverge_fraction is not None else 1.0)
        rank = {"behind": 3.0, "crossed": 2.5, "ahead": 2.0, "closed": 1.0}.get(d.outcome, 0.0)
        rows.append(DriftRow(
            key=k, family=_family(k), outcome=d.outcome, diverge_fraction=d.diverge_fraction,
            final_gap=d.final_gap, max_gap=d.max_gap, threshold=d.threshold,
            sentence=d.sentence.replace("candidate", "The candidate", 1), shape=shape, shape_notes=notes,
            lr_note=d.lr_note, fits=d.fits, score=rank + min(mag, 10) / 5 + early + 0.3 * len(notes),
        ))  # fmt: skip
    rows.sort(key=lambda r: -r.score)
    return {
        "rows": [asdict(r) for r in rows],
        "aligned_by": "fraction of the schedule" if by_fraction else "step",
        "readings": readings(rows, by_fraction),
        "summary": _summary(rows),
    }


def _summary(rows: list[DriftRow]) -> str:
    moved = [r for r in rows if r.outcome not in ("same", "insufficient")]
    if not moved:
        return f"All {len(rows)} shared losses progress like the baseline's within their noise."
    n = {o: sum(r.outcome == o for r in moved) for o in ("behind", "ahead", "crossed", "closed")}
    parts = [f"{v} {o}" for o, v in n.items() if v]
    return f"{len(moved)} of {len(rows)} losses progress differently ({', '.join(parts)}; ahead = lower than the baseline)."


def readings(rows: list[DriftRow], by_fraction: bool = False) -> list[str]:
    """What the pattern across losses says about the training (hypotheses with their evidence)."""
    out: list[str] = []
    moved = [r for r in rows if r.outcome not in ("same", "insufficient") and r.diverge_fraction is not None]
    if by_fraction:
        out.append("The schedules differ in length: curves are compared on the fraction of the schedule.")
    if not moved:
        slow = [r for r in rows if any(n.startswith("converges") for n in r.shape_notes)]
        out.append("No loss drifts beyond its noise: the change does not alter how the losses progress."
                   + (f" Only the convergence speed differs for {', '.join(r.key for r in slow[:4])}." if slow else ""))  # fmt: skip
        return out
    start = [
        r
        for r in moved
        if r.diverge_fraction is not None and r.diverge_fraction < thresholds.t("loss_drift", "from_start")
    ]
    if start:
        fits = next((r.fits for r in start if r.fits), [])
        out.append(
            f"{len(start)} loss(es) differ from the first steps ({', '.join(r.key for r in start[:4])}): that fits a "
            "change of architecture, data, loss definition or initialisation rather than the schedule"
            + (f" — config changes that fit: {'; '.join(fits[:3])}" if fits else "")
            + "."
        )
    later = sorted((r for r in moved if r not in start), key=lambda r: r.diverge_fraction or 0.0)
    clusters: list[list[DriftRow]] = []
    for r in later:
        if clusters and (r.diverge_fraction or 0) - (clusters[-1][0].diverge_fraction or 0) <= thresholds.t(
            "loss_drift", "together_window"
        ):
            clusters[-1].append(r)
        else:
            clusters.append([r])
    for c in clusters:
        if len(c) >= 2:
            lr = next((r.lr_note for r in c if r.lr_note), "")
            out.append(
                f"{len(c)} losses start to drift together at ~{c[0].diverge_fraction:.0%} of training "
                f"({', '.join(r.key for r in c[:4])}{'…' if len(c) > 4 else ''}): a shared cause at that point"
                + (f" — {lr}" if lr else " (no LR event there: look at the data order, a stage switch or a term "
                   "that turns on)") + "."
            )  # fmt: skip
    fams = {r.family for r in moved}
    all_fams = {r.family for r in rows}
    if 1 <= len(fams) <= 2 and len(all_fams) > len(fams) + 1:
        out.append(
            f"Only {', '.join(sorted(fams))} drift{'s' if len(fams) == 1 else ''}; the other {len(all_fams) - len(fams)}"
            " loss families progress like the baseline: the change acts on that head / branch."
        )
    closed = [r for r in moved if r.outcome == "closed"]
    if closed:
        out.append(
            f"{len(closed)} loss(es) differ during training but end at the baseline's value "
            f"({', '.join(r.key for r in closed[:4])}): the change affects the speed, not the optimum."
        )
    crossed = [r for r in moved if r.outcome == "crossed"]
    if crossed:
        out.append(
            f"{len(crossed)} loss(es) cross the baseline ({', '.join(r.key for r in crossed[:4])}): an early "
            "advantage that does not last (or the reverse) — compare at the end, and check the schedule's end phase."
        )
    layered = [r for r in moved if re.search(r"layer=\d", r.key)]
    if layered:
        worst = max(layered, key=lambda r: -(r.final_gap or 0))
        if (worst.final_gap or 0) < 0:
            out.append(f"Per-layer losses drift too; the largest gap is {worst.key} ({worst.final_gap:+.1%} at the end)"
                       " — compare the decoder-layer readouts of both runs.")  # fmt: skip
    return out
