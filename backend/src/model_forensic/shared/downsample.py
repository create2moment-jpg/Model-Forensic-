"""Shape-preserving downsampling for charts.

A plain stride (every n-th point) hides exactly what forensics looks for: a single NaN, a loss spike, a dip. M4
keeps, per bucket, the first, last, minimum and maximum point, and additionally the first non-finite point, so a
chart of 2,000 points shows every spike and every NaN of a 100k-point series.
"""

from __future__ import annotations

import itertools

import numpy as np
import numpy.typing as npt


def m4_indices(values: npt.NDArray[np.float64], max_points: int) -> npt.NDArray[np.int64]:
    """Sorted indices to keep; all of them when the series already fits."""
    n = int(values.size)
    if n <= max_points or max_points < 8:
        return np.arange(n, dtype=np.int64)
    buckets = max(1, max_points // 4)
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    keep: list[int] = []
    finite = np.isfinite(values)
    for a, b in itertools.pairwise(edges.tolist()):
        if b <= a:
            continue
        keep += [int(a), int(b - 1)]
        seg_ok = finite[a:b]
        if seg_ok.any():
            seg = np.where(seg_ok, values[a:b], np.nan)
            keep += [int(a + np.nanargmin(seg)), int(a + np.nanargmax(seg))]
        if not seg_ok.all():
            keep.append(int(a + np.argmin(seg_ok)))  # first NaN / inf in the bucket
    return np.unique(np.asarray(keep, dtype=np.int64))
