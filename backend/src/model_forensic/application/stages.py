"""Pipeline runs: their metrics, configs and logs live on the stage (child) runs.

Azure ML pipelines (e.g. ``train_torch`` → ``evaluate_torch``, or pretrain → finetune with an evaluation after
each) log every metric on a step job, so a pipeline's own run history is nearly empty. Opening the pipeline
should show what its stages logged:

* a metric logged by exactly one stage keeps its key (``train/loss``), with the stage in provenance;
* a metric logged by several stages (the same evaluation after pretraining and after finetuning) is kept once
  per stage with a ``stage=<name>`` dimension, and the **last** stage's value also keeps the plain key, so the
  headline of a pipeline is its final result. ``stage`` is a diagnostic dimension: ordinary comparisons skip it,
  the ``stage_delta`` analysis uses it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from model_forensic.domain.metrics import MetricKey, MetricSeries

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def stage_label(name: str) -> str:
    return _SAFE.sub("_", name)[:60] or "stage"


def merge_stage_series(
    own: Sequence[MetricSeries], stages: Sequence[tuple[str, Sequence[MetricSeries]]]
) -> list[MetricSeries]:
    """``stages`` in pipeline order. The pipeline's own series come first and are never re-keyed."""
    out: dict[MetricKey, MetricSeries] = {s.key: s for s in own}
    by_key: dict[MetricKey, list[tuple[str, MetricSeries]]] = {}
    order = {stage: i for i, (stage, _) in enumerate(stages)}
    for stage, series in stages:
        for s in series:
            if "stage" in s.key.dim_dict:
                continue
            by_key.setdefault(s.key, []).append((stage, s))
    for key, hits in by_key.items():
        if key in out:
            continue
        stamped = [
            s.model_copy(update={"provenance": {**s.provenance, "stage": stage, "stage_index": str(order[stage])}})
            for stage, s in hits
        ]
        if len(hits) == 1:
            out[key] = stamped[0]
            continue
        for (stage, _), s in zip(hits, stamped, strict=True):
            k = MetricKey(name=key.name, split=key.split, dims=(*key.dims, ("stage", stage_label(stage))))
            out[k] = s.model_copy(update={"key": k})
        out[key] = stamped[-1]
    return list(out.values())
