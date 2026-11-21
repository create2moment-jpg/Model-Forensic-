"""Normalize framework loss dicts into per-term and per-decoder-layer metrics.

Handles the common conventions of DETR-style / sparse 3D detectors:

* MMDetection3D heads:  ``loss_cls``, ``loss_bbox`` (last layer) + ``d0.loss_cls`` … ``d4.loss_bbox`` (earlier layers),
  denoising ``dn_loss_cls`` / ``d0.dn_loss_bbox``, encoder ``enc_loss_cls``, plus anything else (``loss_depth``).
* Suffix-indexed (Sparse4D-style): ``loss_cls_0`` … ``loss_cls_5``, ``loss_box_3``.

Output (canonical names, ``train`` split):
``train/loss`` (total), ``train/loss/<term>`` (summed over layers), ``train/loss/<term>[layer=k]`` and
``train/loss/<term>[branch=dn,layer=k]`` for denoising / encoder branches.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Tuple

from model_forensic_instrument.naming import metric_name

_PREFIX_LAYER = re.compile(r"^d(\d+)\.(.+)$")
_SUFFIX_LAYER = re.compile(r"^(.+?)_(\d+)$")
_TERM_ALIASES = {
    "bbox": "bbox",
    "box": "bbox",
    "reg": "bbox",
    "cls": "cls",
    "class": "cls",
    "iou": "iou",
    "giou": "iou",
}


def _value(v: object) -> Optional[float]:
    try:
        if hasattr(v, "detach"):
            v = v.detach()  # type: ignore[union-attr]
        if hasattr(v, "numel") and v.numel() != 1:  # type: ignore[union-attr]
            v = v.float().mean()  # type: ignore[union-attr]
        return float(v.item() if hasattr(v, "item") else v)  # type: ignore[union-attr]
    except (TypeError, ValueError, RuntimeError):
        return None


def _parse(key: str) -> Tuple[str, Optional[int], Optional[str]]:
    """Return (term, layer, branch) for one loss key."""
    k = key
    layer: Optional[int] = None
    m = _PREFIX_LAYER.match(k)
    if m:
        layer, k = int(m.group(1)), m.group(2)
    branch = None
    for b in ("dn", "enc", "aux"):
        if k.startswith(b + "_"):
            branch, k = b, k[len(b) + 1 :]
    if k.startswith("loss_"):
        k = k[5:]
    elif k == "loss":
        k = "total"
    if layer is None:
        m = _SUFFIX_LAYER.match(k)
        if m:
            k, layer = m.group(1), int(m.group(2))
    return _TERM_ALIASES.get(k, k), layer, branch


def normalize_losses(losses: Mapping[str, object], split: str = "train") -> Dict[str, float]:
    """Map a loss dict to canonical metrics. Non-loss entries (``acc``, ``grad_norm``) are ignored."""
    parsed = []
    for key, v in losses.items():
        if "loss" not in key:
            continue
        val = _value(v)
        if val is None:
            continue
        term, layer, branch = _parse(key)
        parsed.append((term, layer, branch, val))

    # MMDet convention: unprefixed main-branch losses are the LAST layer when d0..dN exist for that term.
    max_layer: Dict[Tuple[str, Optional[str]], int] = {}
    for term, layer, branch, _ in parsed:
        if layer is not None:
            max_layer[(term, branch)] = max(max_layer.get((term, branch), -1), layer)
    has_prefixed = any(_PREFIX_LAYER.match(k) for k in losses)

    out: Dict[str, float] = {}
    per_term: Dict[str, float] = {}
    total = 0.0
    for term, layer, branch, val in parsed:
        if term == "total":
            continue
        if layer is None and has_prefixed and (term, branch) in max_layer:
            layer = max_layer[(term, branch)] + 1
        dims: Dict[str, object] = {}
        if layer is not None:
            dims["layer"] = layer
        if branch:
            dims["branch"] = branch
        total += val
        if dims:
            out[metric_name(f"loss/{term}", split, **dims)] = val
        if not branch:
            per_term[term] = per_term.get(term, 0.0) + val
        else:
            per_term[f"{branch}_{term}"] = per_term.get(f"{branch}_{term}", 0.0) + val
    for term, val in per_term.items():
        out[metric_name(f"loss/{term}", split)] = val
    reported_total = next((_value(v) for k, v in losses.items() if k == "loss"), None)
    out[metric_name("loss", split)] = reported_total if reported_total is not None else total
    return out
