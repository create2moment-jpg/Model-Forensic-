"""Matching instability of a set-prediction (DETR-style) head: does the Hungarian matching keep assigning the same
query to the same object — between epochs, and between decoder layers?

DN-DETR (Li et al., 2022) measures how often the matching of a fixed validation batch changes from one epoch to the
next; *Detection Transformer with Stable Matching* (Liu et al., 2023) the same between decoder layers. Unstable
matching means the queries chase different targets, so the layer-by-layer refinement and the box losses cannot
settle. Denoising queries are meant to reduce it.

    mt = MatchTracker()
    # every N epochs, on ONE fixed validation batch, for each decoder layer k (and each sample i of the batch):
    rows, cols = linear_sum_assignment(cost_k[i])          # query indices, ground-truth indices
    mt.record(layer=k, query_idx=rows, gt_idx=cols, sample=i)
    fx.log(mt.flush(), step)                               # → val/match_instability[layer=k], val/match_layer_change[layer=k]

* ``val/match_instability[layer=k]`` — share of the ground-truth objects whose matched query differs from the
  previous ``flush`` (same sample, same layer). 0 = the same query keeps the same object.
* ``val/match_layer_change[layer=k]`` — share of the objects whose matched query at layer *k* differs from layer
  *k−1* in this measurement (k ≥ 1).
* ``val/match_count[layer=k]`` — matched objects compared (sanity: the batch must be fixed).

Pure Python; works with lists, numpy arrays or tensors (``.tolist()``).
"""

from __future__ import annotations

from typing import Any, Dict, Hashable, Optional, Tuple

from model_forensic_instrument.naming import metric_name


def _ints(x: Any) -> list:
    if hasattr(x, "detach"):
        x = x.detach().cpu()
    if hasattr(x, "tolist"):
        x = x.tolist()
    return [int(v) for v in x]


class MatchTracker:
    """Collects one measurement's matchings (``record``) and turns them into instability metrics (``flush``)."""

    def __init__(self, split: str = "val") -> None:
        self.split = split
        self._now: Dict[Tuple[Hashable, str], Dict[int, int]] = {}  # (sample, layer) → {gt: query}
        self._prev: Dict[Tuple[Hashable, str], Dict[int, int]] = {}

    def record(self, layer: Any, query_idx: Any, gt_idx: Any, sample: Hashable = 0) -> None:
        """One layer's matching for one sample: pairs ``query_idx[i]`` ↔ ``gt_idx[i]`` (scipy's
        ``linear_sum_assignment`` output, or MMDet's assigned indices)."""
        q, g = _ints(query_idx), _ints(gt_idx)
        if len(q) != len(g):
            raise ValueError(f"query_idx and gt_idx differ in length ({len(q)} vs {len(g)})")
        self._now[(sample, str(layer))] = {gi: qi for qi, gi in zip(q, g) if gi >= 0 and qi >= 0}

    def record_assignment(self, layer: Any, assigned_gt: Any, sample: Hashable = 0) -> None:
        """Same, from a per-query array: ``assigned_gt[q]`` = the matched ground truth, or -1 (unmatched)."""
        a = _ints(assigned_gt)
        self.record(layer, [qi for qi, gi in enumerate(a) if gi >= 0], [gi for gi in a if gi >= 0], sample)

    def flush(self, step: Optional[int] = None) -> Dict[str, float]:
        """Metrics of this measurement against the previous one; this one becomes the previous."""
        del step
        out: Dict[str, float] = {}
        layers = sorted({lay for _, lay in self._now}, key=lambda x: (0, int(x)) if x.lstrip("-").isdigit() else (1, x))
        for lay in layers:
            changed = total = 0
            for (sample, la), now in self._now.items():
                if la != lay:
                    continue
                before = self._prev.get((sample, la))
                if before is None:
                    continue
                for gt, qi in now.items():
                    if gt in before:
                        total += 1
                        changed += int(before[gt] != qi)
            if total:
                out[metric_name("match_instability", self.split, layer=lay)] = changed / total
                out[metric_name("match_count", self.split, layer=lay)] = float(total)
        for i in range(1, len(layers)):
            lo, hi = layers[i - 1], layers[i]
            changed = total = 0
            for (sample, la), now in self._now.items():
                if la != hi:
                    continue
                below = self._now.get((sample, lo), {})
                for gt, qi in now.items():
                    if gt in below:
                        total += 1
                        changed += int(below[gt] != qi)
            if total:
                out[metric_name("match_layer_change", self.split, layer=hi)] = changed / total
        self._prev, self._now = self._now, {}
        return out
