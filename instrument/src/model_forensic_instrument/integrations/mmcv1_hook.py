"""mmcv 1.x / MMDetection3D 0.17 integration (StreamPETR, PETR, BEVFormer, ... codebases).

Config::

    custom_imports = dict(imports=["model_forensic_instrument.integrations.mmcv1_hook"], allow_failed_imports=False)
    custom_hooks = [dict(type="ForensicMmcvHook", priority="LOW",
                         groups=dict(camera="img_backbone", neck="img_neck",
                                     head="pts_bbox_head", decoder="pts_bbox_head.transformer.decoder"),
                         features=dict(img_neck="img_neck"),
                         every=50)]

How it works with mmcv 1.x: ``OptimizerHook.after_train_iter`` runs zero_grad → backward → (clip) → step, and the
gradients stay on the parameters until the next iteration. This hook therefore copies the weights in
``before_train_iter`` and, with ``priority="LOW"`` (after the optimizer hook), reads gradients and the actual update
in ``after_train_iter``. With ``Fp16OptimizerHook`` the gradients are still scaled unless ``grad_clip`` is set; the
hook divides by the loss scaler's scale in that case (the scale may have been updated in the same step — gradient
norms are then off by that factor of 2 for one log point). Validation metrics written to ``runner.log_buffer`` by
the eval hook are mapped to canonical names (``pts_bbox_NuScenes/car_AP_dist_2.0`` → ``val/AP[class=car,thr=2.0]``).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from model_forensic_instrument.forensic import Forensic
from model_forensic_instrument.naming import metric_name
from model_forensic_instrument.sinks import JsonlSink

try:
    from mmcv.runner import HOOKS, Hook
except ImportError:  # pragma: no cover - mmcv 1.x optional
    Hook = object  # type: ignore[assignment,misc]
    HOOKS = None

_NUS_HEADLINE = {
    "NDS": "NDS",
    "mAP": "mAP",
    "mATE": "mATE",
    "mASE": "mASE",
    "mAOE": "mAOE",
    "mAVE": "mAVE",
    "mAAE": "mAAE",
    "amota": "AMOTA",
    "amotp": "AMOTP",
    "ids": "IDS",
    "recall": "recall",
    "motar": "MOTAR",
}
_NUS_ERR = {"trans_err": "ATE", "scale_err": "ASE", "orient_err": "AOE", "vel_err": "AVE", "attr_err": "AAE"}
_AP = re.compile(r"^(?P<cls>[a-z_]+?)_AP_dist_(?P<thr>[\d.]+)$")
_ERR = re.compile(r"^(?P<cls>[a-z_]+?)_(?P<err>trans_err|scale_err|orient_err|vel_err|attr_err)$")


def map_mmdet3d_metric(key: str, split: str = "val") -> Optional[str]:
    """``pts_bbox_NuScenes/car_AP_dist_2.0`` → ``val/AP[class=car,thr=2.0]``; returns None for non-metrics."""
    name = key.split("/")[-1]
    if name in _NUS_HEADLINE:
        return metric_name(_NUS_HEADLINE[name], split)
    m = _AP.match(name)
    if m:
        return metric_name("AP", split, **{"class": m["cls"], "thr": m["thr"]})
    m = _ERR.match(name)
    if m:
        return metric_name(_NUS_ERR[m["err"]], split, **{"class": m["cls"]})
    return None


class ForensicMmcvHook(Hook):  # type: ignore[misc,valid-type]
    def __init__(
        self,
        groups: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, str]] = None,
        every: int = 50,
        feature_every: Optional[int] = None,
        log_losses: bool = True,
        jsonl_path: Optional[str] = None,
        probe_shared: Optional[Any] = None,
        probe_every: int = 500,
    ) -> None:
        self.cfg = dict(groups=groups, features=features, every=every, feature_every=feature_every)
        self.log_losses = log_losses
        self.jsonl_path = jsonl_path
        self.fx: Optional[Forensic] = None
        self._logged_val: Dict[str, float] = {}
        if probe_shared is not None:  # gradients per loss term need the loss tensors; not available from mmcv hooks
            raise ValueError("probe_shared is not supported by the mmcv hook; call LossGradProbe in the head's loss()")

    def before_run(self, runner: Any) -> None:
        model = runner.model.module if hasattr(runner.model, "module") else runner.model
        grad_scale = None
        for h in getattr(runner, "hooks", []):
            if "Fp16OptimizerHook" in type(h).__name__ and getattr(h, "grad_clip", None) is None:
                scaler = getattr(h, "loss_scaler", None)
                if scaler is not None and hasattr(scaler, "get_scale"):
                    grad_scale = lambda s=scaler: float(s.get_scale())  # noqa: E731
                elif scaler is not None and hasattr(scaler, "loss_scale"):
                    grad_scale = lambda s=scaler: float(s.loss_scale)  # noqa: E731
        sink = JsonlSink(self.jsonl_path) if self.jsonl_path else None
        self.fx = Forensic(model, grad_scale=grad_scale, sink=sink, **self.cfg)  # type: ignore[arg-type]

    def before_train_iter(self, runner: Any) -> None:
        if self.fx is None:
            return
        self.fx.start_step(runner.iter)
        if self.fx.grads is not None and self.fx.enabled:
            self.fx.grads.snapshot(runner.iter)

    def after_train_iter(self, runner: Any) -> None:
        if self.fx is None or not self.fx.enabled:
            return
        it = runner.iter
        out: Dict[str, float] = {}
        if self.fx.grads is not None:
            out.update(self.fx.grads.grad_stats(it))
            out.update(self.fx.grads.after_step(it))
        if self.fx.features is not None:
            out.update(self.fx.features.collect())
        if out:
            self.fx.log(out, it)
        if self.log_losses:
            log_vars = (getattr(runner, "outputs", None) or {}).get("log_vars", {})
            if log_vars:
                self.fx.log_losses(log_vars, it)
        self._log_val(runner)

    def after_train_epoch(self, runner: Any) -> None:
        self._log_val(runner)

    def _log_val(self, runner: Any) -> None:
        buf = getattr(getattr(runner, "log_buffer", None), "output", None) or {}
        metrics = {}
        for k, v in buf.items():
            name = map_mmdet3d_metric(k)
            if name is not None and self._logged_val.get(name) != v:
                try:
                    metrics[name] = float(v)
                except (TypeError, ValueError):
                    continue
        if metrics and self.fx is not None:
            self._logged_val.update(metrics)
            self.fx.log(metrics, runner.iter)

    def after_run(self, runner: Any) -> None:
        if self.fx is not None:
            self.fx.close()


if HOOKS is not None:  # pragma: no cover - mmcv 1.x optional
    HOOKS.register_module(module=ForensicMmcvHook)
