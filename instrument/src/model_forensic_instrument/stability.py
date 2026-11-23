"""Early warnings of a training instability, before the loss shows it (Wortsman et al., 2024, *Small-scale proxies
for large-scale Transformer training instabilities*): attention logits that keep growing (queries and keys get
large) and output logits that drift. QK-layernorm, capping the attention logits (Moonshot's QK-Clip) or a smaller
AdamW ε are the usual remedies.

* ``attn_logit_stats(q, k, layer)`` — from the queries and keys of one attention block (``[..., L, d]``, before the
  softmax): ``train/attn_logit_max[layer=k]`` (largest |q·k|/√d, on at most ``max_rows`` query rows) and
  ``train/q_norm[layer=k]`` / ``train/k_norm[layer=k]`` (mean L2 norm per token). Call it where your attention
  computes q and k (fused / flash attention never shows the logits), on logging steps only.
* ``output_logit_stats(logits, layer)`` — the classification head's raw outputs: ``train/logit_max[layer=k]``
  (largest |logit|), ``train/logit_rms[layer=k]`` and ``train/logit_mean[layer=k]`` (drift away from 0).

Works with numpy arrays or tensors (numpy required).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Union

from model_forensic_instrument.naming import metric_name


def _np(x: Any) -> Any:
    import numpy as np

    if hasattr(x, "detach"):
        x = x.detach().float().cpu().numpy()
    return np.asarray(x, dtype=float)


def attn_logit_stats(
    q: Any, k: Any, layer: Union[int, str], split: str = "train", max_rows: int = 256, scale: Optional[float] = None
) -> Dict[str, float]:
    """Largest attention logit and the q / k norms of one attention block (see the module docstring)."""
    import numpy as np

    qa, ka = _np(q), _np(k)
    if qa.ndim < 2 or ka.ndim < 2 or qa.shape[-1] != ka.shape[-1]:
        raise ValueError("q and k must be [..., L, d] with the same d")
    d = qa.shape[-1]
    qa = qa.reshape(-1, qa.shape[-2], d)
    ka = ka.reshape(-1, ka.shape[-2], d)
    if ka.shape[0] != qa.shape[0]:
        ka = np.broadcast_to(ka[:1], (qa.shape[0],) + ka.shape[1:])
    rows = max(1, max_rows // max(1, qa.shape[0]))
    s = (1.0 / math.sqrt(d)) if scale is None else scale
    logits = np.einsum("bqd,bkd->bqk", qa[:, :rows], ka) * s
    lay = str(layer)
    return {
        metric_name("attn_logit_max", split, layer=lay): float(np.abs(logits).max()) if logits.size else 0.0,
        metric_name("q_norm", split, layer=lay): float(np.linalg.norm(qa, axis=-1).mean()),
        metric_name("k_norm", split, layer=lay): float(np.linalg.norm(ka, axis=-1).mean()),
    }


def output_logit_stats(logits: Any, layer: Union[int, str, None] = None, split: str = "train") -> Dict[str, float]:
    """Size and drift of a head's raw logits (see the module docstring)."""
    import numpy as np

    x = _np(logits)
    x = x[np.isfinite(x)]
    if not x.size:
        return {}
    dims = {"layer": str(layer)} if layer is not None else {}
    return {
        metric_name("logit_max", split, **dims): float(np.abs(x).max()),
        metric_name("logit_rms", split, **dims): float(np.sqrt((x**2).mean())),
        metric_name("logit_mean", split, **dims): float(x.mean()),
    }
