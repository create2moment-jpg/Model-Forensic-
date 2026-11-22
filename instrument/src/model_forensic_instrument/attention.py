"""Decoder internals: where cross-attention looks, per layer and per modality, and how old the queries are.

* ``attention_stats``: from cross-attention weights ``[..., Q, K]`` (rows sum to 1) and the token ranges of each
  modality, per decoder layer:

  - ``train/attn_mass[layer=k,modality=m]`` — share of the attention on modality *m*'s tokens (mean over queries):
    what each layer actually reads. A modality near 0 in every layer is ignored by the decoder.
  - ``train/attn_entropy[layer=k]`` — entropy normalised to 0 (one token) … 1 (uniform over all tokens);
    ``train/attn_entropy[layer=k,modality=m]`` the same inside the modality's tokens. Collapse → 0, no focus → 1.
  - ``train/attn_top1[layer=k]`` — mean of the largest weight per query (how peaked the attention is).

  Pass ``positive`` (bool per query: matched to ground truth) to measure the queries that matter.

* ``AttentionRecorder``: forward hooks on ``nn.MultiheadAttention`` modules (one per decoder layer's cross-attention)
  that read the head-averaged weights PyTorch returns (``need_weights=True`` is its default; fused / flash attention
  never materialises them — switch it off on recording steps or call ``attention_stats`` yourself). Records every
  ``every`` steps only.

* ``query_age_stats``: ages (frames since a query was created; 0 = new this frame) of the queries →
  ``train/q_age_mean[source=all|matched]`` and ``train/q_age_frac[age=0|1-3|4-inf]`` (of the matched queries when
  ``positive`` is given). Track ages yourself: new queries 0, propagated ones carry ``age + 1`` (StreamPETR: next to
  the memory embedding).

Works with torch tensors or numpy arrays (numpy is needed; torch only for the recorder).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from model_forensic_instrument.naming import metric_name
from model_forensic_instrument.temporal import DEFAULT_BUCKETS, seqpos_bucket

Tokens = Union[slice, Sequence[int], Any]  # a slice, indices, or a bool mask over K


def _np(x: Any) -> Any:
    import numpy as np

    if hasattr(x, "detach"):
        x = x.detach().float().cpu().numpy()
    return np.asarray(x, dtype=float)


def _mask(sel: Tokens, k: int) -> Any:
    import numpy as np

    m = np.zeros(k, dtype=bool)
    if isinstance(sel, slice):
        m[sel] = True
        return m
    arr = _np(sel) if hasattr(sel, "detach") else np.asarray(sel)
    if arr.dtype == bool:
        m[: arr.size] = arr.reshape(-1)[:k]
    else:
        m[arr.astype(int).reshape(-1)] = True
    return m


def _entropy(p: Any, axis: int = -1) -> Any:
    import numpy as np

    q = np.clip(p, 1e-12, None)
    return -(p * np.log(q)).sum(axis=axis)


def attention_stats(
    weights: Any,
    token_groups: Mapping[str, Tokens],
    layer: Union[int, str],
    split: str = "train",
    positive: Any = None,
) -> Dict[str, float]:
    """Attention mass / entropy / peak per modality for one decoder layer (see the module docstring)."""
    import numpy as np

    w = _np(weights)
    if w.ndim < 2:
        raise ValueError("attention weights must be [..., Q, K]")
    k = w.shape[-1]
    w = w.reshape(-1, w.shape[-2], k)  # [batch*heads, Q, K]
    rows = w.reshape(-1, k)
    if positive is not None:  # one bool per query (shared by batch / heads) or one per row
        pos = _np(positive).astype(bool).reshape(-1)
        if pos.size == w.shape[-2]:
            pos = np.tile(pos, w.shape[0])
        if pos.size == rows.shape[0]:
            rows = rows[pos]
    if rows.shape[0] == 0:
        return {}
    rows = rows / np.clip(rows.sum(axis=-1, keepdims=True), 1e-12, None)
    out: Dict[str, float] = {}
    lay = str(layer)
    out[metric_name("attn_entropy", split, layer=lay)] = float(_entropy(rows).mean() / math.log(k)) if k > 1 else 0.0
    out[metric_name("attn_top1", split, layer=lay)] = float(rows.max(axis=-1).mean())
    for mod, sel in token_groups.items():
        m = _mask(sel, k)
        n = int(m.sum())
        if n == 0:
            continue
        part = rows[:, m]
        mass = part.sum(axis=-1)
        out[metric_name("attn_mass", split, layer=lay, modality=mod)] = float(mass.mean())
        if n > 1:
            inner = part / np.clip(mass[:, None], 1e-12, None)
            out[metric_name("attn_entropy", split, layer=lay, modality=mod)] = float(
                _entropy(inner).mean() / math.log(n)
            )
    return out


def query_age_stats(
    ages: Any,
    positive: Any = None,
    split: str = "train",
    buckets: Sequence[Tuple[str, int, Optional[int]]] = DEFAULT_BUCKETS,
) -> Dict[str, float]:
    """Query ages → mean age (all / matched) and the share per age bucket (of the matched queries if given)."""
    import numpy as np

    a = _np(ages).reshape(-1)
    out: Dict[str, float] = {}
    if a.size == 0:
        return out
    out[metric_name("q_age_mean", split, source="all")] = float(a.mean())
    sel = a
    if positive is not None:
        pos = _np(positive).astype(bool).reshape(-1)
        if pos.size == a.size:
            sel = a[pos]
            if sel.size:
                out[metric_name("q_age_mean", split, source="matched")] = float(sel.mean())
    if sel.size:
        names = [seqpos_bucket(int(x), buckets) for x in sel]
        for b, _, _ in buckets:
            out[metric_name("q_age_frac", split, age=b)] = float(np.mean([n == b for n in names]))
    return out


class AttentionRecorder:
    """Forward hooks on the cross-attention module of each decoder layer; ``flush`` returns the stats.

    ``layers``: ``{layer index: module path}`` (exact names from ``model.named_modules()``), e.g.
    ``{0: "pts_bbox_head.transformer.decoder.layers.0.attentions.1.attn", …}`` — the ``nn.MultiheadAttention`` that
    attends to the sensor tokens. ``token_groups``: the modality ranges over the keys, or a callable returning them
    (when the number of tokens changes per batch); ``set_groups`` updates them per step.
    """

    def __init__(
        self,
        model: Any,
        layers: Mapping[Union[int, str], str],
        token_groups: Union[Mapping[str, Tokens], Callable[[], Mapping[str, Tokens]], None] = None,
        every: int = 200,
        split: str = "train",
    ) -> None:
        mods = dict(model.named_modules())
        missing = [p for p in layers.values() if p not in mods]
        if missing:
            raise KeyError(f"attention modules not found: {missing} (see model.named_modules())")
        self.every, self.split = every, split
        self._groups = token_groups
        self._armed = False
        self._positive: Any = None
        self._stats: Dict[str, List[float]] = {}
        self._handles = [mods[p].register_forward_hook(self._hook(str(k))) for k, p in layers.items()]

    def set_groups(self, token_groups: Mapping[str, Tokens], positive: Any = None) -> None:
        self._groups = token_groups
        self._positive = positive

    def start_step(self, step: int) -> None:
        self._armed = step % self.every == 0

    def _hook(self, layer: str) -> Callable[..., None]:
        def hook(_module: Any, _inputs: Any, output: Any) -> None:
            if not self._armed or not isinstance(output, tuple) or len(output) < 2 or output[1] is None:
                return
            groups = self._groups() if callable(self._groups) else self._groups
            if not groups:
                return
            for k, v in attention_stats(output[1], groups, layer, self.split, self._positive).items():
                self._stats.setdefault(k, []).append(v)

        return hook

    def flush(self) -> Dict[str, float]:
        out = {k: sum(v) / len(v) for k, v in self._stats.items() if v}
        self._stats.clear()
        self._armed = False
        return out

    def remove(self) -> None:
        for h in self._handles:
            h.remove()
        self._handles = []
