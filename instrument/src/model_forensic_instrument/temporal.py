"""Temporal / streaming-query diagnostics (StreamPETR-style memory queues).

* ``SequencePositionMeter``: averages losses by position in the sequence (frames since the stream was reset:
  0 = no memory, ``1-3``, ``4-inf``) and emits ``train/loss/<term>[seqpos=…]`` every N steps. If frames with history
  are not clearly better than frames without, the memory is not helping.
* ``query_source_stats``: of the queries matched to ground truth, how many came from the propagated memory vs
  fresh queries — ``train/q_pos_frac[source=propagated|new]`` and ``train/q_from_memory_frac``.

For evaluation, slice metrics by sequence position with ``evaluate_by_slices`` (``dim="seqpos"``).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from model_forensic_instrument.losses import normalize_losses
from model_forensic_instrument.naming import metric_name, with_dims

DEFAULT_BUCKETS: Tuple[Tuple[str, int, Optional[int]], ...] = (("0", 0, 0), ("1-3", 1, 3), ("4-inf", 4, None))


def seqpos_bucket(pos: int, buckets: Sequence[Tuple[str, int, Optional[int]]] = DEFAULT_BUCKETS) -> str:
    for name, lo, hi in buckets:
        if pos >= lo and (hi is None or pos <= hi):
            return name
    return str(pos)


class SequencePositionMeter:
    """Accumulate per-term losses by sequence-position bucket; ``flush`` returns window means.

    Track ``pos`` yourself, e.g. ``pos = 0 if not img_metas[0]["prev_exists"] else pos + 1`` (StreamPETR datasets
    expose ``prev_exists``). With batch size > 1 per GPU use the minimum position in the batch.
    """

    def __init__(self, buckets: Sequence[Tuple[str, int, Optional[int]]] = DEFAULT_BUCKETS) -> None:
        self.buckets = buckets
        self._sum: Dict[Tuple[str, str], float] = defaultdict(float)
        self._n: Dict[Tuple[str, str], int] = defaultdict(int)

    def add(self, losses: Mapping[str, Any], pos: int) -> None:
        b = seqpos_bucket(pos, self.buckets)
        for k, v in normalize_losses(losses).items():
            if "[" in k:  # per-layer / per-branch detail is not needed per bucket
                continue
            self._sum[(k, b)] += v
            self._n[(k, b)] += 1

    def flush(self) -> Dict[str, float]:
        out = {with_dims(k, seqpos=b): self._sum[(k, b)] / n for (k, b), n in self._n.items() if n}
        self._sum.clear()
        self._n.clear()
        return out


def query_source_stats(positive_mask: Any, propagated_mask: Any, split: str = "train") -> Dict[str, float]:
    """``positive_mask``: bool [Q] (or [B, Q]) — queries matched to a ground-truth object.
    ``propagated_mask``: bool, same shape — queries that came from the temporal memory.

    In StreamPETR the propagated queries are concatenated with the fresh ones; build the mask from that layout.
    """
    import torch

    with torch.no_grad():
        pos = positive_mask.bool().reshape(-1)
        prop = propagated_mask.bool().reshape(-1)
        out: Dict[str, float] = {}
        n_prop, n_new = int(prop.sum()), int((~prop).sum())
        if n_prop:
            out[metric_name("q_pos_frac", split, source="propagated")] = float((pos & prop).sum()) / n_prop
        if n_new:
            out[metric_name("q_pos_frac", split, source="new")] = float((pos & ~prop).sum()) / n_new
        if int(pos.sum()):
            out[metric_name("q_from_memory_frac", split)] = float((pos & prop).sum()) / float(pos.sum())
        return out
