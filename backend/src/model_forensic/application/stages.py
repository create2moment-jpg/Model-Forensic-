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


def metric_catalog(series: Sequence[MetricSeries], stage_names: Sequence[str]) -> dict[str, object]:
    """Metrics per stage, for browsing a pipeline as ``stage / metric``.

    Every entry names the stage that logged it (``None`` for the run's own metrics or a plain run). The plain-key copy
    of the last stage's value (kept for headlines and comparisons) is left out when per-stage keys exist, so nothing
    is listed twice. ``stages`` keeps the pipeline order (only stages that logged something).
    """
    by_label = {stage_label(n): n for n in stage_names}

    def without_stage(k: MetricKey) -> MetricKey:
        return MetricKey(name=k.name, split=k.split, dims=tuple((a, b) for a, b in k.dims if a != "stage"))

    per_stage = {without_stage(s.key) for s in series if "stage" in s.key.dim_dict}
    entries: list[dict[str, str | None]] = []
    for s in series:
        dims = s.key.dim_dict
        stage: str | None
        if "stage" in dims:
            stage = by_label.get(dims["stage"], dims["stage"])
            metric = str(without_stage(s.key))
        else:
            if s.key in per_stage:
                continue  # the last stage's plain copy: listed under its stage already
            stage = s.provenance.get("stage") or None
            metric = str(s.key)
        entries.append({"key": str(s.key), "metric": metric, "stage": stage,
                        "label": f"{stage} / {metric}" if stage else metric})  # fmt: skip
    used = {e["stage"] for e in entries if e["stage"]}
    order = [n for n in stage_names if n in used] + sorted(str(x) for x in used if x not in stage_names)
    entries.sort(key=lambda e: (order.index(e["stage"]) if e["stage"] in order else -1, str(e["metric"])))
    return {"stages": order, "entries": entries}


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
