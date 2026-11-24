"""A comparison as a Markdown block for a pull request or a chat message: the verdict sentence, the headline metrics
(baseline, candidate, change, whether it is meaningful), what changed in the config, and how to reproduce it.

Deterministic and built from the same comparison the Compare page shows — nothing in it the page does not say.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from model_forensic.application.headline import headline
from model_forensic.application.verdict import compare_verdict, metric_label

_MARK = {"improved": "✅ better", "regressed": "❌ worse", "unchanged": "≈ within noise", "unknown": "?"}


def _fmt(x: float) -> str:
    if not math.isfinite(x):
        return str(x)
    a = abs(x)
    return f"{x:.3g}" if a >= 1000 or (a and a < 0.001) else f"{x:.4g}"


def _name(svc: Any, ref: Any) -> str:
    try:
        r = svc.get_run(ref)
        return str(r.display_name or r.name or ref)
    except Exception:
        return str(ref)


def compare_markdown(svc: Any, baseline: str, candidate: str, stage: str | None = None, max_rows: int = 12) -> str:
    v = compare_verdict(svc, baseline, candidate, stage)
    cmp = svc.compare(baseline, candidate, stage=stage)
    keys = headline([d.metric for d in cmp.deltas], svc.team.overview, svc.team.deprioritized_classes)["keys"]
    order = {k: i for i, k in enumerate(keys)}
    rows = sorted(cmp.deltas, key=lambda d: (d.metric not in order, order.get(d.metric, 0), -abs(d.relative or 0)))
    b, c = svc.ref(baseline), svc.ref(candidate)
    out = [
        f"### {_name(svc, c)} vs {_name(svc, b)}" + (f" · stage `{stage}`" if stage else ""),
        "",
        f"**{v['sentence']}**",
        "",
    ]
    if rows:
        out += ["| metric | baseline | candidate | change | |", "|---|---:|---:|---:|---|"]
        for d in rows[:max_rows]:
            rel = f"{100 * d.relative:+.1f}%" if d.relative is not None and math.isfinite(d.relative) else "—"
            out.append(
                f"| {metric_label(d.metric)} | {_fmt(d.baseline)} | {_fmt(d.candidate)} | {rel} | {_MARK.get(d.verdict.value, '')} |"
            )
        if len(rows) > max_rows:
            out.append(f"| … {len(rows) - max_rows} more | | | | |")
        out.append("")
    out += curves(svc, b, c, stage, keys)
    changes = list(cmp.config_diff.meaningful)
    if changes:
        out.append("**What changed in the config**")
        out.append("")
        out += [f"- `{ch.describe()}`" for ch in changes[:8]]
        if len(changes) > 8:
            out.append(f"- … {len(changes) - 8} more")
        out.append("")
    basis = (f"significance from {len(cmp.baseline_replicates) + 1} baseline seeds" if len(cmp.baseline_replicates) >= 2
             else "single seed: threshold-based significance")  # fmt: skip
    cmd = f"mf summary --baseline {b} --candidate {c}" + (f" --stage {stage}" if stage else "")
    out += [f"<sub>{basis} · `{b}` → `{c}` · reproduce: `{cmd}` · Model Forensic</sub>", ""]
    return "\n".join(out)


_BARS = "▁▂▃▄▅▆▇█"


def spark_text(values: Sequence[float], lo: float, hi: float, width: int = 24) -> str:
    """A curve as text (``▇▆▅▃▂▁``) on a given scale — shows in any PR, chat or terminal without an image."""
    v = [x for x in values if math.isfinite(x)]
    if len(v) < 2:
        return ""
    idx = sorted({round(i * (len(v) - 1) / (min(width, len(v)) - 1)) for i in range(min(width, len(v)))})
    span = (hi - lo) or 1.0
    return "".join(_BARS[min(7, max(0, int((v[i] - lo) / span * 7.999)))] for i in idx)


def curves(svc: Any, b: Any, c: Any, stage: str | None, keys: Sequence[str], n: int = 3) -> list[str]:
    """Baseline and candidate curves of the training loss and the headline metrics that have curves, each pair on one
    shared scale (so the two lines can be read against each other)."""
    from model_forensic.analysis.series_ops import total_loss

    def view(ref: Any) -> dict[str, Any]:
        st = stage if stage and stage in [name for name, _ in svc.stages(ref)] else None
        return {str(s.key): s for s in svc.stage_series(ref, st)}

    try:
        bs, cs = view(b), view(c)
    except Exception:  # the histories are not readable now: the table above still stands
        return []
    want = [k for k in [total_loss(cs), *keys] if k]
    rows = []
    for k in dict.fromkeys(want):
        if k not in bs or k not in cs:
            continue
        bv, cv = [float(x) for x in bs[k].finite()[1]], [float(x) for x in cs[k].finite()[1]]
        if len(bv) < 3 or len(cv) < 3:
            continue  # a value per stage is not a curve
        both = sorted(bv + cv)  # a robust scale: a few spikes clip at the top instead of flattening the curve
        lo, hi = both[int(0.02 * (len(both) - 1))], both[int(0.98 * (len(both) - 1))]
        rows.append(f"| {metric_label(k)} | `{spark_text(bv, lo, hi)}` | `{spark_text(cv, lo, hi)}` |")
        if len(rows) >= n:
            break
    if not rows:
        return []
    return ["**Curves** (baseline vs candidate, one scale per metric, start → end; spikes clip at the top)", "",
            "| metric | baseline | candidate |", "|---|---|---|", *rows, ""]  # fmt: skip
