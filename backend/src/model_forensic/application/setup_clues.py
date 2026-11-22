"""Training Setup Clues — how a run was set up, read from its configs, metrics and compute, each with its source.

Clues are found by generic key patterns (optimizer, learning rate, batch size, schedule, precision, clipping, seed,
workers, devices, accumulation, EMA, epochs / steps, loss weighting, dataset version) — never by a project's key
names. Every clue cites where it came from (``config:<path>``, ``metric:<key>``, ``run:compute``). Observations
combine clues (effective batch size, warm-up seen in the LR curve, no seed set, mixed precision without clipping).
A clue that is not found is listed as *not found* — never guessed.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from model_forensic.domain.metrics import Split

PATTERNS: list[tuple[str, str, str]] = [
    ("optimizer", "Optimizer", r"(^|\.)(optimi[sz]er)(\.(_target_|name|type|class_path))?$"),
    ("lr", "Learning rate", r"(^|\.)(lr|learning_rate|base_lr|max_lr)$"),
    ("batch_size", "Batch size", r"(^|\.)(train_)?batch_size(_per_(gpu|device))?$"),
    ("scheduler", "LR schedule", r"(^|\.)(lr_)?scheduler(\.(_target_|name|type|class_path))?$"),
    ("warmup", "Warm-up", r"(^|\.)warm_?up(_steps|_epochs|_iters|_ratio)?$"),
    ("weight_decay", "Weight decay", r"(^|\.)weight_decay$"),
    ("precision", "Precision", r"(^|\.)(precision|amp|mixed_precision|fp16|bf16)$"),
    ("grad_clip", "Gradient clipping", r"(^|\.)(gradient_clip_val|grad_clip|clip_grad(_norm)?|max_grad_norm)$"),
    ("seed", "Seed", r"(^|\.)(seed|random_seed)$"),
    ("num_workers", "Data loader workers", r"(^|\.)num_workers$"),
    ("devices", "Devices", r"(^|\.)(devices|gpus|num_gpus)$"),
    ("nodes", "Nodes", r"(^|\.)(num_nodes|nodes)$"),
    ("accumulate", "Gradient accumulation", r"(^|\.)(accumulate_grad_batches|grad_accum(ulation)?(_steps)?)$"),
    ("ema", "EMA of weights", r"(^|\.)(ema|use_ema|ema_decay)$"),
    ("max_epochs", "Epochs", r"(^|\.)(max_epochs|epochs|num_epochs)$"),
    ("max_steps", "Steps", r"(^|\.)(max_steps|total_steps|num_steps|max_iters)$"),
    ("loss_weighting", "Loss weighting", r"(uncertainty_weighting|loss_weight(ing)?|log_sigma)"),
    ("dataset_version", "Dataset version", r"(^|\.)(dataset_version|data_version)(\.|$)"),
]


def _num(v: Any) -> float | None:
    try:
        return float(v) if not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def clues(svc: Any, run: str) -> dict[str, Any]:
    ref = svc.ref(run)
    r = svc.get_run(ref)
    try:
        cfg = svc.flat_config(ref)
    except Exception:
        cfg = {}
    found: dict[str, dict[str, Any]] = {}
    for cid, label, pat in PATTERNS:
        rx = re.compile(pat, re.I)
        hits = [(k, v) for k, v in cfg.items() if rx.search(k) and v is not None and not isinstance(v, dict)]
        if hits:
            hits.sort(key=lambda kv: (kv[0].count("."), kv[0]))
            k, v = hits[0]
            found[cid] = {"id": cid, "label": label, "value": v if isinstance(v, (int, float, str, bool)) else str(v),
                          "source": f"config:{k}", "also": [f"config:{x}" for x, _ in hits[1:4]]}  # fmt: skip
    if r.compute.gpu_count and "devices" not in found:
        found["devices"] = {
            "id": "devices",
            "label": "Devices",
            "value": r.compute.gpu_count,
            "source": "run:compute",
            "also": [],
        }
    if r.compute.node_count and "nodes" not in found:
        found["nodes"] = {
            "id": "nodes",
            "label": "Nodes",
            "value": r.compute.node_count,
            "source": "run:compute",
            "also": [],
        }
    if r.compute.gpu_type:
        found["gpu_type"] = {
            "id": "gpu_type",
            "label": "GPU type",
            "value": r.compute.gpu_type,
            "source": "run:compute",
            "also": [],
        }
    obs: list[dict[str, Any]] = []
    series = {str(s.key): s for s in svc.series(ref)}
    lr = next(
        (
            s
            for k, s in series.items()
            if s.key.split == Split.TRAIN and s.key.name in ("lr", "learning_rate") and not s.key.dims
        ),
        None,
    )
    if lr is not None:
        _, v = lr.finite()
        if v.size >= 5:
            peak = int(np.argmax(v))
            found.setdefault(
                "lr",
                {
                    "id": "lr",
                    "label": "Learning rate",
                    "value": float(v.max()),
                    "source": f"metric:{lr.key}",
                    "also": [],
                },
            )
            if peak > 0 and v[0] < 0.5 * v[peak]:
                obs.append({"id": "warmup_seen", "severity": "info", "text": f"the LR rises for the first {peak / max(1, v.size - 1):.0%} of the "
                            f"logged steps (warm-up) to {v[peak]:.3g}", "evidence": [f"metric:{lr.key}"]})  # fmt: skip
            obs.append({"id": "lr_end", "severity": "info", "text": f"the LR ends at {v[-1]:.3g} ({v[-1] / v[peak]:.1%} of its peak)" if v[peak] else
                        "the LR is zero throughout", "evidence": [f"metric:{lr.key}"]})  # fmt: skip
    bs, dev = _num(found.get("batch_size", {}).get("value")), _num(found.get("devices", {}).get("value"))
    nodes, acc = _num(found.get("nodes", {}).get("value")) or 1.0, _num(found.get("accumulate", {}).get("value")) or 1.0
    if bs and dev:
        eff = bs * dev * nodes * acc
        obs.append({"id": "effective_batch", "severity": "info", "text": f"effective batch {eff:g} = {bs:g} per device × {dev:g} device(s)"
                    f" × {nodes:g} node(s) × {acc:g} accumulation", "evidence": [found["batch_size"]["source"], found["devices"]["source"]]})  # fmt: skip
    if "seed" not in found:
        obs.append({"id": "no_seed", "severity": "warning", "text": "no seed in the configuration — repeated runs may differ by noise alone",
                    "evidence": []})  # fmt: skip
    prec = str(found.get("precision", {}).get("value", "")).lower()
    if prec and any(t in prec for t in ("16", "mixed", "true")) and "grad_clip" not in found:
        obs.append({"id": "amp_no_clip", "severity": "info", "text": f"reduced precision ({prec}) without gradient clipping in the config",
                    "evidence": [found["precision"]["source"]]})  # fmt: skip
    order = [cid for cid, _, _ in PATTERNS] + ["gpu_type"]
    missing = [label for cid, label, _ in PATTERNS if cid not in found]
    return {
        "run": str(ref),
        "clues": [found[c] for c in order if c in found],
        "not_found": missing,
        "observations": obs,
        "config_keys": len(cfg),
        "note": "" if cfg else "no configuration was found for this run — clues come from metrics and compute only",
    }
