"""PyTorch monitors: per-module gradient / update health and feature statistics.

Torch is imported lazily so the package can be installed anywhere.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

GroupSpec = Union[str, Sequence[str], Callable[[str], bool]]


def _matcher(spec: GroupSpec) -> Callable[[str], bool]:
    if callable(spec):
        return spec
    prefixes = [spec] if isinstance(spec, str) else list(spec)
    return lambda name: any(name == p or name.startswith(p + ".") for p in prefixes)


def resolve_groups(model: Any, groups: Mapping[str, GroupSpec]) -> Dict[str, List[Tuple[str, Any]]]:
    """Assign every trainable parameter to the first matching group (or ``other``)."""
    matchers = [(g, _matcher(s)) for g, s in groups.items()]
    out: Dict[str, List[Tuple[str, Any]]] = {g: [] for g in groups}
    out["other"] = []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        g = next((g for g, m in matchers if m(name)), "other")
        out[g].append((name, p))
    if not out["other"]:
        del out["other"]
    empty = [g for g, ps in out.items() if not ps]
    if empty:
        raise ValueError(
            f"module groups matched no trainable parameters: {empty}. "
            f"Check the prefixes against model.named_parameters()."
        )
    return out


def layer_groups(prefix: str, n: int, name: str = "dec_layer") -> Dict[str, str]:
    """One module group per decoder layer — ``{"dec_layer0": "<prefix>.0", …}`` — so gradient norms and update
    ratios are logged per layer (which layers learn, which barely move). Merge with your other groups."""
    return {f"{name}{i}": f"{prefix}.{i}" for i in range(n)}


class GradMonitor:
    """Per-group gradient norm, parameter norm, dead-gradient fraction and actual update ratio.

    * ``train/grad_norm[module=g]``     L2 norm of the group's gradients (unscaled: pass ``grad_scale`` with AMP)
    * ``train/param_norm[module=g]``    L2 norm of the group's parameters
    * ``train/grad_dead_frac[module=g]`` fraction of the group's parameter tensors with no / all-zero gradient
    * ``train/update_ratio[module=g]``  ||Δθ|| / ||θ|| of the optimizer step (the "is it learning?" signal;
      healthy values are typically ~1e-4…1e-2 for AdamW)
    """

    def __init__(
        self,
        model: Any,
        groups: Mapping[str, GroupSpec],
        every: int = 50,
        track_updates: bool = True,
        grad_scale: Optional[Callable[[], float]] = None,
    ) -> None:
        self.groups = resolve_groups(model, groups)
        self.every = max(1, int(every))
        self.track_updates = track_updates
        self.grad_scale = grad_scale
        self._snapshot: Optional[Dict[str, List[Any]]] = None
        self._snapshot_step: Optional[int] = None

    def due(self, step: int) -> bool:
        return step % self.every == 0

    def grad_stats(self, step: int) -> Dict[str, float]:
        """Gradient / parameter norms and dead fraction per group (gradients must be present)."""
        if not self.due(step):
            return {}
        import torch

        scale = float(self.grad_scale()) if self.grad_scale else 1.0
        out: Dict[str, float] = {}
        with torch.no_grad():
            for g, params in self.groups.items():
                gsq = psq = 0.0
                dead = 0
                for _, p in params:
                    psq += float(p.detach().float().pow(2).sum())
                    if p.grad is None:
                        dead += 1
                        continue
                    n = float(p.grad.detach().float().pow(2).sum())
                    if n == 0.0:
                        dead += 1
                    gsq += n
                out[f"train/grad_norm[module={g}]"] = (gsq**0.5) / scale
                out[f"train/param_norm[module={g}]"] = psq**0.5
                out[f"train/grad_dead_frac[module={g}]"] = dead / max(1, len(params))
        return out

    def snapshot(self, step: int) -> None:
        """Copy the weights now, so ``after_step`` can measure the optimizer update."""
        if not (self.track_updates and self.due(step)):
            return
        import torch

        with torch.no_grad():
            self._snapshot = {g: [p.detach().clone() for _, p in params] for g, params in self.groups.items()}
        self._snapshot_step = step

    def after_backward(self, step: int) -> Dict[str, float]:
        out = self.grad_stats(step)
        self.snapshot(step)
        return out

    def after_step(self, step: int) -> Dict[str, float]:
        if self._snapshot is None or self._snapshot_step != step:
            return {}
        import torch

        out: Dict[str, float] = {}
        with torch.no_grad():
            for g, params in self.groups.items():
                dsq = psq = 0.0
                for (_, p), before in zip(params, self._snapshot[g]):
                    dsq += float((p.detach() - before).float().pow(2).sum())
                    psq += float(before.float().pow(2).sum())
                out[f"train/update_ratio[module={g}]"] = (dsq**0.5) / max(psq**0.5, 1e-12)
        self._snapshot = None
        return out


def _tensors(output: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    """Yield (scale_label, tensor) from a tensor, list/tuple (multi-scale FPN) or dict output."""
    import torch

    if isinstance(output, torch.Tensor):
        yield prefix, output
    elif isinstance(output, (list, tuple)):
        for i, o in enumerate(output):
            yield from _tensors(o, f"{prefix}{i}" if not prefix else f"{prefix}.{i}")
    elif isinstance(output, dict):
        for k, o in output.items():
            yield from _tensors(o, f"{prefix}{k}" if not prefix else f"{prefix}.{k}")


class FeatureMonitor:
    """Statistics of selected modules' outputs (e.g. each modality's neck, each FPN scale, the fused BEV).

    Logs ``train/feat_rms[module=m,scale=s]``, ``train/feat_std[...]``, ``train/feat_zero_frac[...]`` and
    ``train/feat_nonfinite_frac[...]`` every ``every`` steps. Multi-scale outputs (lists/tuples/dicts of
    tensors) get one ``scale`` per entry. Hooks are cheap no-ops on other steps.
    """

    def __init__(self, model: Any, modules: Mapping[str, str], every: int = 200) -> None:
        named = dict(model.named_modules())
        missing = [p for p in modules.values() if p not in named]
        if missing:
            raise ValueError(f"modules not found in model: {missing}")
        self.every = max(1, int(every))
        self._active = False
        self._buffer: Dict[str, float] = {}
        self._handles = [named[path].register_forward_hook(self._hook(label)) for label, path in modules.items()]

    def _hook(self, label: str) -> Callable[..., None]:
        def hook(_module: Any, _inp: Any, output: Any) -> None:
            if not self._active:
                return
            import torch

            with torch.no_grad():
                for scale, t in _tensors(output):
                    if not t.is_floating_point() or t.numel() == 0:
                        continue
                    x = t.detach().float()
                    finite = torch.isfinite(x)
                    nonfinite = 1.0 - float(finite.float().mean())
                    x = torch.where(finite, x, torch.zeros_like(x))
                    dims = f"module={label}" + (f",scale={scale}" if scale != "" else "")
                    self._buffer[f"train/feat_rms[{dims}]"] = float(x.pow(2).mean().sqrt())
                    self._buffer[f"train/feat_std[{dims}]"] = float(x.std()) if x.numel() > 1 else 0.0
                    self._buffer[f"train/feat_zero_frac[{dims}]"] = float((x.abs() < 1e-8).float().mean())
                    self._buffer[f"train/feat_nonfinite_frac[{dims}]"] = nonfinite

        return hook

    def start_step(self, step: int) -> None:
        self._active = step % self.every == 0
        self._buffer = {}

    def collect(self) -> Dict[str, float]:
        out, self._buffer, self._active = self._buffer, {}, False
        return out

    def remove(self) -> None:
        for h in self._handles:
            h.remove()


@contextlib.contextmanager
def no_monitoring(*monitors: Any):  # type: ignore[no-untyped-def]
    """Temporarily disable feature hooks (e.g. during evaluation)."""
    states = [(m, getattr(m, "_active", False)) for m in monitors]
    for m, _ in states:
        m._active = False
    try:
        yield
    finally:
        for m, s in states:
            m._active = s
