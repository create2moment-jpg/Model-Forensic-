"""Tiny dependency-free SVG charts for self-contained reports (theme-aware through CSS variables)."""

from __future__ import annotations

import html
import math
from collections.abc import Sequence

PALETTE = ("#4f7cff", "#e0565b", "#2fb37e", "#c58b2b", "#9b6bdf", "#1fb5c9", "#d9669c", "#7a8699")
W, H = 640, 300
L, R, T, B = 58, 16, 30, 44


def _fmt(v: float) -> str:
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 1000 or a < 0.01:
        return f"{v:.1e}"
    return f"{v:.3g}"


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        return [lo]
    step = 10 ** math.floor(math.log10((hi - lo) / n))
    for m in (1, 2, 5, 10):
        if (hi - lo) / (step * m) <= n:
            step *= m
            break
    start = math.ceil(lo / step) * step
    out: list[float] = []
    v = float(start)
    while v <= hi + 1e-12 and len(out) < 12:
        out.append(round(v, 12))
        v += step
    return out


def _frame(title: str, xlabel: str, ylabel: str, xt: list[tuple[float, str]], yt: list[tuple[float, str]]) -> list[str]:
    e = html.escape
    out = [
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{e(title)}">',
        f'<text class="ct" x="{L}" y="18">{e(title)}</text>',
    ]
    for y, lab in yt:
        out.append(
            f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">{e(lab)}</text>'
        )
    for x, lab in xt:
        out.append(f'<text class="tick" x="{x:.1f}" y="{H - B + 16}" text-anchor="middle">{e(lab)}</text>')
    out.append(f'<text class="axis" x="{(L + W - R) / 2}" y="{H - 8}" text-anchor="middle">{e(xlabel)}</text>')
    out.append(
        f'<text class="axis" transform="translate(14,{(T + H - B) / 2}) rotate(-90)" text-anchor="middle">{e(ylabel)}</text>'
    )
    return out


def _legend(names: Sequence[str]) -> list[str]:
    out: list[str] = []
    x = float(L)
    for i, n in enumerate(names):
        out.append(
            f'<rect x="{x}" y="{T - 4}" width="10" height="3" fill="{PALETTE[i % len(PALETTE)]}"/>'
            f'<text class="leg" x="{x + 14}" y="{T}">{html.escape(n[:28])}</text>'
        )
        x += 24 + 6.2 * min(len(n), 28)
    return out


def line_chart(
    series: Sequence[tuple[str, Sequence[float], Sequence[float]]],
    title: str,
    xlabel: str,
    ylabel: str,
    log_y: bool = False,
) -> str:
    pts = [
        (x, y)
        for _, xs, ys in series
        for x, y in zip(xs, ys, strict=False)
        if math.isfinite(x) and math.isfinite(y) and (y > 0 or not log_y)
    ]
    if not pts:
        return ""
    tf = (lambda v: math.log10(v)) if log_y else (lambda v: v)
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(tf(p[1]) for p in pts), max(tf(p[1]) for p in pts)
    if y1 == y0:
        y0, y1 = y0 - 1, y1 + 1
    pad = (y1 - y0) * 0.06
    y0, y1 = y0 - pad, y1 + pad
    x1 = x1 if x1 > x0 else x0 + 1

    def sx(v: float) -> float:
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def sy(v: float) -> float:
        return H - B - (tf(v) - y0) / (y1 - y0) * (H - B - T - 8)

    yt = [(H - B - (t - y0) / (y1 - y0) * (H - B - T - 8), _fmt(10**t if log_y else t)) for t in _ticks(y0, y1)]
    xt = [(sx(t), _fmt(t)) for t in _ticks(x0, x1)]
    out = _frame(title, xlabel, ylabel + (" (log)" if log_y else ""), xt, yt) + _legend([s[0] for s in series])
    for i, (_, xs, ys) in enumerate(series):
        p = [
            (sx(x), sy(y))
            for x, y in zip(xs, ys, strict=False)
            if math.isfinite(x) and math.isfinite(y) and (y > 0 or not log_y)
        ]
        if not p:
            continue
        c = PALETTE[i % len(PALETTE)]
        out.append(
            f'<polyline fill="none" stroke="{c}" stroke-width="1.8" points="'
            + " ".join(f"{a:.1f},{b:.1f}" for a, b in p)
            + '"/>'
        )
        if len(p) <= 12:
            out += [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.6" fill="{c}"/>' for a, b in p]
    out.append("</svg>")
    return "".join(out)


def bar_chart(
    categories: Sequence[str],
    groups: Sequence[tuple[str, Sequence[float]]],
    title: str,
    ylabel: str,
    percent: bool = False,
) -> str:
    vals = [v for _, vs in groups for v in vs if math.isfinite(v)]
    if not categories or not vals:
        return ""
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    hi = hi if hi > lo else lo + 1

    def sy(v: float) -> float:
        return H - B - (v - lo) / (hi - lo) * (H - B - T - 8)

    yt = [(sy(t), f"{t:.0%}" if percent else _fmt(t)) for t in _ticks(lo, hi)]
    slot = (W - L - R) / len(categories)
    bw = slot * 0.8 / max(1, len(groups))
    xt = [(L + slot * (i + 0.5), c[:14]) for i, c in enumerate(categories)]
    out = _frame(title, "", ylabel, xt, yt) + _legend([g[0] for g in groups])
    for gi, (_, vs) in enumerate(groups):
        c = PALETTE[gi % len(PALETTE)]
        for i, v in enumerate(vs):
            if not math.isfinite(v):
                continue
            x = L + slot * i + slot * 0.1 + gi * bw
            y, y_zero = sy(max(v, 0)), sy(min(v, 0))
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 1:.1f}" height="{max(0.5, y_zero - y):.1f}" fill="{c}">'
                f"<title>{html.escape(categories[i])}: {v:.3g}</title></rect>"
            )
    out.append(f'<line class="zero" x1="{L}" x2="{W - R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}"/></svg>')
    return "".join(out)
