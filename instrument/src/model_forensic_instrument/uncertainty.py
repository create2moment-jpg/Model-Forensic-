"""Uncertainty diagnostics for regression heads that predict a scale (aleatoric uncertainty).

Given residuals ``r = pred - target`` of matched objects and the predicted scale (Laplace ``b`` or Gaussian
``σ``, or a log-scale / log-variance), ``uncertainty_stats`` returns, per box dimension and optionally per slice
(e.g. range bucket):

* ``unc_abs_err``  mean |r|                          — the actual error
* ``unc_scale``    mean predicted scale (b or σ)      — what the model claims
* ``unc_nerr``     calibration ratio: Laplace mean |r|/b, Gaussian mean r²/σ² (both 1.0 when calibrated;
                   > 1 over-confident, < 1 under-confident)
* ``unc_cov``      empirical coverage of the central 68% / 95% interval (``level=68|95``)
* ``unc_corr``     Spearman rank correlation of scale with |r| (does uncertainty rank errors?)
* ``unc_clamped_frac`` fraction of scales at the clamp bounds, if ``clamp`` is given

Log them on train batches (every N steps) and at evaluation; Model Forensic's ``uncertainty_health`` analysis
detects loss attenuation (scale inflating instead of error shrinking), miscalibration per range, uninformative
uncertainty and clamp saturation.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from model_forensic_instrument.naming import metric_name

# Interval half-widths in units of the scale for central coverage p.
_Q = {
    "laplace": {68: math.log(1 / 0.32), 95: math.log(1 / 0.05)},  # P(|r| <= t b) = 1 - exp(-t)
    "gaussian": {68: 0.99446, 95: 1.95996},
}


def _ranks(x: Any) -> Any:
    import torch

    order = torch.argsort(x)
    r = torch.empty_like(order, dtype=torch.float32)
    r[order] = torch.arange(x.numel(), dtype=torch.float32, device=x.device)
    return r


def _spearman(a: Any, b: Any) -> float:
    if a.numel() < 3:
        return float("nan")
    ra, rb = _ranks(a), _ranks(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    den = (ra.pow(2).sum() * rb.pow(2).sum()).sqrt()
    return float((ra * rb).sum() / den) if float(den) > 0 else float("nan")


def uncertainty_stats(
    residual: Any,
    scale: Any,
    kind: str = "laplace",
    scale_is_log: bool = False,
    log_variance: bool = False,
    dims: Optional[Sequence[str]] = None,
    slices: Optional[Mapping[str, Any]] = None,
    slice_dim: str = "range",
    clamp: Optional[Tuple[float, float]] = None,
    split: str = "val",
) -> Dict[str, float]:
    """Compute calibration diagnostics. ``residual`` and ``scale`` are [N, D] (or [N]) tensors of matched objects.

    ``scale_is_log``: scale holds log b / log σ. ``log_variance``: scale holds log σ² (Gaussian heads often predict
    this). ``dims`` names the D columns (e.g. ``["x", "y", "z", "w", "l", "h", "vx", "vy"]``). ``slices`` maps a
    slice name to a boolean mask over N (e.g. range buckets from object distance). ``clamp`` is (min, max) of the
    scale as clamped by the head.
    """
    import torch

    if kind not in _Q:
        raise ValueError("kind must be 'laplace' or 'gaussian'")
    with torch.no_grad():
        r = residual.detach().float()
        s = scale.detach().float()
        if r.dim() == 1:
            r, s = r[:, None], s[:, None]
        if log_variance:
            s = (0.5 * s).exp()
        elif scale_is_log:
            s = s.exp()
        s = s.clamp_min(1e-12)
        names = list(dims) if dims else [str(i) for i in range(r.shape[1])]
        if len(names) != r.shape[1]:
            raise ValueError(f"dims has {len(names)} names but residual has {r.shape[1]} columns")
        groups: Dict[str, Any] = {"": torch.ones(r.shape[0], dtype=torch.bool, device=r.device)}
        for name, mask in (slices or {}).items():
            groups[name] = mask.to(device=r.device, dtype=torch.bool)
        out: Dict[str, float] = {}
        for gname, mask in groups.items():
            if int(mask.sum()) == 0:
                continue
            for j, dname in enumerate(names):
                a, b = r[mask, j].abs(), s[mask, j]
                finite = torch.isfinite(a) & torch.isfinite(b)
                a, b = a[finite], b[finite]
                if a.numel() == 0:
                    continue
                dd: Dict[str, object] = {"dim": dname}
                if gname:
                    dd[slice_dim] = gname
                nerr = (a / b).mean() if kind == "laplace" else (a.pow(2) / b.pow(2)).mean()
                out[metric_name("unc_abs_err", split, **dd)] = float(a.mean())
                out[metric_name("unc_scale", split, **dd)] = float(b.mean())
                out[metric_name("unc_nerr", split, **dd)] = float(nerr)
                for level, t in _Q[kind].items():
                    out[metric_name("unc_cov", split, **dd, level=level)] = float((a <= t * b).float().mean())
                corr = _spearman(b, a)
                if corr == corr:
                    out[metric_name("unc_corr", split, **dd)] = corr
                if clamp is not None:
                    lo, hi = clamp
                    near = (b <= lo * 1.001) | (b >= hi * 0.999)
                    out[metric_name("unc_clamped_frac", split, **dd)] = float(near.float().mean())
        return out
