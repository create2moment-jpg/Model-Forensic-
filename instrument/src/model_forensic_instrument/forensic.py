"""One object to wire the kit into a training loop."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Mapping, Optional

from model_forensic_instrument.losses import normalize_losses
from model_forensic_instrument.sinks import Sink, default_sink
from model_forensic_instrument.torch_monitors import FeatureMonitor, GradMonitor, GroupSpec


def _is_main_process() -> bool:
    try:
        import torch.distributed as dist

        if dist.is_available() and dist.is_initialized():
            return dist.get_rank() == 0
    except ImportError:
        pass
    return os.environ.get("RANK", "0") in ("0", "")


class Forensic:
    """Architecture diagnostics for one training run.

    Args:
        model: the ``nn.Module`` (unwrap DDP: pass ``model.module``).
        groups: module groups for gradient/update health, e.g. ``{"camera": "img_backbone",
            "radar": "radar_encoder", "fusion": "fusion", "decoder": "pts_bbox_head.transformer.decoder"}``.
            Values are parameter-name prefixes (or lists, or predicates). Unmatched params → ``other``.
        features: modules whose outputs to summarize, ``{"radar_fpn": "radar_neck", "bev": "fusion"}``
            (exact names from ``model.named_modules()``). Multi-scale outputs get a ``scale`` dimension.
        every: log gradient/update stats every N steps; ``feature_every`` for features (default 4×every).
        sink: where metrics go (default: MLflow if installed — i.e. the Azure ML job — else a local jsonl).
        grad_scale: with fp16 AMP, ``lambda: scaler.get_scale()`` if you call ``after_backward`` before
            ``scaler.unscale_`` (otherwise leave None).
    """

    def __init__(
        self,
        model: Any,
        groups: Optional[Mapping[str, GroupSpec]] = None,
        features: Optional[Mapping[str, str]] = None,
        every: int = 50,
        feature_every: Optional[int] = None,
        sink: Optional[Sink] = None,
        grad_scale: Optional[Callable[[], float]] = None,
        track_updates: bool = True,
        main_process_only: bool = True,
    ) -> None:
        self.enabled = _is_main_process() or not main_process_only
        self.sink = sink or default_sink()
        self.grads = GradMonitor(model, groups, every, track_updates, grad_scale) if groups else None
        self.features = FeatureMonitor(model, features, feature_every or every * 4) if features else None
        self.every = every

    def start_step(self, step: int) -> None:
        if self.features is not None and self.enabled:
            self.features.start_step(step)

    def after_backward(self, step: int) -> None:
        if self.grads is not None and self.enabled:
            self._emit(self.grads.after_backward(step), step)

    def after_step(self, step: int) -> None:
        if not self.enabled:
            return
        out: Dict[str, float] = {}
        if self.grads is not None:
            out.update(self.grads.after_step(step))
        if self.features is not None:
            out.update(self.features.collect())
        self._emit(out, step)

    def log_losses(self, losses: Mapping[str, Any], step: int, every: Optional[int] = None) -> None:
        if self.enabled and step % (every or self.every) == 0:
            self._emit(normalize_losses(losses), step)

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        if self.enabled:
            self._emit(dict(metrics), step)

    def _emit(self, metrics: Dict[str, float], step: int) -> None:
        if metrics:
            self.sink.log(metrics, step)

    def close(self) -> None:
        if self.features is not None:
            self.features.remove()
        self.sink.flush()
