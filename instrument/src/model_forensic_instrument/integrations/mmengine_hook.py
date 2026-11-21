"""MMEngine / MMDetection3D integration.

Register in the config::

    custom_imports = dict(imports=["model_forensic_instrument.integrations.mmengine_hook"], allow_failed_imports=False)
    custom_hooks = [dict(type="ForensicHook",
                         groups=dict(camera="img_backbone", radar="radar_encoder", fusion="fusion_layer",
                                     decoder="pts_bbox_head.transformer.decoder"),
                         features=dict(radar_fpn="radar_neck", camera_fpn="img_neck"),
                         every=50)]

MMEngine's ``OptimWrapper.update_params`` runs backward, step and zero_grad inside ``train_step``, so a
normal hook never sees gradients. The hook therefore wraps ``optim_wrapper.step`` to measure gradients
just before the optimizer step and updates just after. With ``AmpOptimWrapper`` gradients may still be
scaled at that point; the hook divides by the loss scale unless ``clip_grad`` already unscaled them.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from model_forensic_instrument.forensic import Forensic
from model_forensic_instrument.sinks import JsonlSink

try:
    from mmengine.hooks import Hook
    from mmengine.registry import HOOKS
except ImportError:  # pragma: no cover - mmengine optional
    Hook = object  # type: ignore[assignment,misc]
    HOOKS = None


class ForensicHook(Hook):  # type: ignore[misc,valid-type]
    priority = "LOW"

    def __init__(
        self,
        groups: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, str]] = None,
        every: int = 50,
        feature_every: Optional[int] = None,
        log_losses: bool = True,
        jsonl_path: Optional[str] = None,
    ) -> None:
        self.cfg = dict(groups=groups, features=features, every=every, feature_every=feature_every)
        self.jsonl_path = jsonl_path  # default: MLflow if installed (Azure ML), else outputs/forensic/metrics.jsonl
        self.log_losses = log_losses
        self.fx: Optional[Forensic] = None

    def before_train(self, runner: Any) -> None:
        model = runner.model.module if hasattr(runner.model, "module") else runner.model
        wrapper = runner.optim_wrapper
        scaler = getattr(wrapper, "loss_scaler", None)
        clip = bool(getattr(wrapper, "clip_grad_kwargs", None))
        grad_scale = (lambda: float(scaler.get_scale())) if scaler is not None and not clip else None
        sink = JsonlSink(self.jsonl_path) if self.jsonl_path else None
        self.fx = Forensic(model, grad_scale=grad_scale, sink=sink, **self.cfg)  # type: ignore[arg-type]
        original_step = wrapper.step
        hook = self

        def step(*args: Any, **kwargs: Any) -> Any:
            it = runner.iter
            assert hook.fx is not None
            hook.fx.after_backward(it)
            result = original_step(*args, **kwargs)
            hook.fx.after_step(it)
            return result

        wrapper.step = step

    def before_train_iter(self, runner: Any, batch_idx: int, data_batch: Any = None) -> None:
        if self.fx is not None:
            self.fx.start_step(runner.iter)

    def after_train_iter(self, runner: Any, batch_idx: int, data_batch: Any = None, outputs: Any = None) -> None:
        if self.fx is not None and self.log_losses and isinstance(outputs, dict):
            self.fx.log_losses(outputs, runner.iter)

    def after_val_epoch(self, runner: Any, metrics: Optional[Dict[str, float]] = None) -> None:
        if self.fx is None or not metrics:
            return
        flat = {}
        for k, v in metrics.items():
            name = k.split("/")[-1]  # e.g. "NuScenes metric/pred_instances_3d_NuScenes/NDS" → "NDS"
            try:
                flat[f"val/{name}"] = float(v)
            except (TypeError, ValueError):
                continue
        self.fx.log(flat, runner.iter)

    def after_train(self, runner: Any) -> None:
        if self.fx is not None:
            self.fx.close()


if HOOKS is not None:  # pragma: no cover - mmengine optional
    HOOKS.register_module(module=ForensicHook)
