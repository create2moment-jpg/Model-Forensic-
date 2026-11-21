"""Evaluation-time modality / module ablations — no retraining.

``ablation_eval`` re-runs your evaluation with one branch's output zeroed (e.g. the radar encoder) and
logs every metric with an ``ablation`` dimension: ``val/NDS[ablation=no_radar]``. The drop vs.
``ablation=none`` is that branch's contribution *for this trained model* — the most direct evidence for
"is the radar branch used?" and "where (range/class) does it help?".

Caveat: zeroing is not the same as the sensor being absent at training time; a model trained with
modality dropout handles zeros better. Treat results as relative contributions.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Sequence, Union

from model_forensic_instrument.naming import with_dims
from model_forensic_instrument.sinks import Sink


def _zeros_like(output: Any) -> Any:
    import torch

    if isinstance(output, torch.Tensor):
        return torch.zeros_like(output)
    if isinstance(output, tuple):
        z = [_zeros_like(o) for o in output]
        return type(output)(*z) if hasattr(output, "_fields") else tuple(z)
    if isinstance(output, list):
        return [_zeros_like(o) for o in output]
    if isinstance(output, dict):
        return {k: _zeros_like(v) for k, v in output.items()}
    return output


@contextlib.contextmanager
def zero_output(model: Any, paths: Union[str, Sequence[str]]) -> Iterator[None]:
    """Within the block, the named modules return zeros of the same structure (tensor / list / tuple / dict)."""
    named = dict(model.named_modules())
    paths = [paths] if isinstance(paths, str) else list(paths)
    missing = [p for p in paths if p not in named]
    if missing:
        raise ValueError(f"modules not found in model: {missing}")
    handles = [named[p].register_forward_hook(lambda _m, _i, out: _zeros_like(out)) for p in paths]
    try:
        yield
    finally:
        for h in handles:
            h.remove()


def ablation_eval(
    model: Any,
    evaluate: Callable[[], Mapping[str, float]],
    ablations: Mapping[str, Union[str, Sequence[str]]],
    sink: Optional[Sink] = None,
    step: int = 0,
    split: str = "val",
    include_reference: bool = True,
) -> Dict[str, Dict[str, float]]:
    """Run ``evaluate()`` normally and once per ablation; log ``<split>/<metric>[ablation=<name>]``.

    ``evaluate`` returns metric names → values; names may already carry slices
    (``"recall[range=60-inf]"``), which are kept. Example::

        ablation_eval(model, run_val, {"no_radar": "radar_encoder", "no_camera": ["img_backbone", "img_neck"]},
                      sink=fx.sink, step=global_step)
    """
    results: Dict[str, Dict[str, float]] = {}
    runs: Dict[str, Optional[Sequence[str]]] = {"none": None} if include_reference else {}
    runs.update({k: ([v] if isinstance(v, str) else list(v)) for k, v in ablations.items()})
    for name, paths in runs.items():
        ctx = zero_output(model, paths) if paths else contextlib.nullcontext()
        with ctx:
            metrics = dict(evaluate())
        results[name] = metrics
        if sink is not None:
            logged = {}
            for k, v in metrics.items():
                full = k if "/" in k.split("[")[0] else f"{split}/{k}"
                logged[with_dims(full, ablation=name)] = float(v)
            sink.log(logged, step)
    return results
