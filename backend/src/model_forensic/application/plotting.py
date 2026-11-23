"""Plotting: suggest, validate and render :class:`~model_forensic.domain.plotspec.PlotSpecV1` specs.

* ``suggest(svc, run)`` — deterministic, from the metric keys the run has (losses, evaluation metrics, learning
  rate, learned task weights); no metric name of a project is hard-coded — families are recognised by key shape.
* ``validate(doc, svc)`` — schema first (pydantic), then grounding: the run exists, each key is one of the run's
  series, a log axis has positive values. Errors are returned, never drawn.
* ``render(spec, svc)`` — resolved, transformed and M4-downsampled data for the UI.
* ``suggest_ai(container, run, question)`` — the local LLM proposes specs as JSON; every one passes ``validate`` or
  is dropped with the reason (grounded: it can only plot keys the run has).
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
from pydantic import ValidationError as PydanticError

from model_forensic.domain.metrics import MetricSeries, Split
from model_forensic.domain.plotspec import PlotSpecV1, SeriesRef
from model_forensic.shared.downsample import m4_indices

MAX_POINTS = 1500


def _series_map(svc: Any, run: str, stage: str = "") -> dict[str, MetricSeries]:
    """The run's series by key; with ``stage``, what that stage logged under the plain keys (stages never mix)."""
    if stage:
        from model_forensic.application.stages import stage_view

        return {str(s.key): s for s in stage_view(svc.series(run), stage)}
    return {str(s.key): s for s in svc.series(run)}


def validate(doc: Any, svc: Any) -> tuple[PlotSpecV1 | None, list[str], list[str]]:
    """(spec or None, errors, warnings). A spec with errors is never rendered."""
    try:
        spec = PlotSpecV1.model_validate(doc)
    except PydanticError as exc:
        errs = [f"{'.'.join(str(p) for p in e['loc']) or 'spec'}: {e['msg']}" for e in exc.errors()[:20]]
        return None, errs, []
    errors: list[str] = []
    warnings: list[str] = []
    cache: dict[tuple[str, str], dict[str, MetricSeries]] = {}
    for i, s in enumerate(spec.series):
        try:
            if s.stage and s.stage not in [n for n, _ in svc.stages(s.run)]:
                errors.append(f"series.{i}.stage: {s.run} has no stage {s.stage!r}")
                continue
            if (s.run, s.stage) not in cache:
                cache[(s.run, s.stage)] = _series_map(svc, s.run, s.stage)
        except Exception as exc:
            errors.append(f"series.{i}.run: {s.run!r} not found ({type(exc).__name__})")
            continue
        got = cache[(s.run, s.stage)]
        found = got.get(s.key)
        if found is None:
            close = [k for k in got if s.key.split("/")[-1] in k][:5]
            errors.append(
                f"series.{i}.key: {s.key!r} is not a metric of {s.run}"
                + (f" in stage {s.stage}" if s.stage else "")
                + (f" (similar: {', '.join(close)})" if close else "")
            )
            continue
        _, v = found.finite()
        if not v.size:
            warnings.append(f"series.{i}: {s.key} has no finite value")
        elif spec.y.scale == "log" and s.transform in ("raw", "ema") and float(np.min(v)) <= 0:
            errors.append(f"y.scale: log axis but {s.key} has values ≤ 0 — use linear")
    return (spec if not errors else None), errors, warnings


def _transform(ref: SeriesRef, v: np.ndarray) -> np.ndarray:
    v = v.astype(float)
    fin = v[np.isfinite(v)]
    if ref.transform == "exp_neg2":
        return np.exp(-2.0 * v)
    if ref.transform in ("normalize", "delta_from_first") and fin.size:
        out_n: np.ndarray = v / fin[0] if ref.transform == "normalize" and fin[0] != 0 else v - fin[0]
        return out_n
    if ref.transform == "ema":
        out = np.full_like(v, np.nan)
        acc = w = 0.0
        for i, x in enumerate(v):
            if np.isfinite(x):
                acc = (1 - ref.ema_alpha) * acc + ref.ema_alpha * x
                w = (1 - ref.ema_alpha) * w + ref.ema_alpha
                out[i] = acc / w
        return out
    return v


def render(spec: PlotSpecV1, svc: Any, max_points: int = MAX_POINTS) -> dict[str, Any]:
    out = []
    for s in spec.series:
        ms = _series_map(svc, s.run, s.stage)[s.key]
        steps = np.asarray(ms.steps, dtype=float)
        vals = _transform(s, np.asarray(ms.values, dtype=float))
        idx = m4_indices(vals, max_points)
        out.append({
            "label": s.label or (s.key if len({x.run for x in spec.series}) == 1 else f"{s.run} · {s.key}"),
            "run": s.run, "key": s.key, "transform": s.transform, "stage": s.stage,
            "x": steps[idx].tolist(), "y": [float(x) if np.isfinite(x) else None for x in vals[idx]],
            "points_total": int(vals.size),
        })  # fmt: skip
    return {"spec": spec.model_dump(), "series": out}


def _family(key: str) -> str:
    name = key.split("/", 1)[-1]
    if name.startswith("task_log_sigma"):
        return "sigma"
    if re.match(r"^(lr|learning_rate)(\b|\[|$)", name):
        return "lr"
    if "loss" in name:
        return "loss"
    return "metric"


def suggest(svc: Any, run: str, stage: str | None = None) -> list[PlotSpecV1]:
    """Rule-based plots for a run, from the shape of its keys (validated like any other spec); ``stage``: of what
    that pipeline stage logged."""
    if stage:
        from model_forensic.application.stages import stage_view

        series = stage_view(svc.series(run), stage)
    else:
        series = svc.series(run)
    st = stage or ""
    series = [s for s in series if s.finite()[1].size >= 3]  # curves only: a value per stage is not a line plot
    by: dict[str, list[MetricSeries]] = {}
    for s in series:
        by.setdefault(_family(str(s.key)), []).append(s)
    rid = str(svc.ref(run))
    specs: list[PlotSpecV1] = []
    losses = sorted(by.get("loss", []), key=lambda s: (s.key.dims != (), s.key.split != Split.TRAIN, str(s.key)))
    headline = [s for s in losses if not s.key.dims][:4]
    if headline:
        pos = all(float(np.nanmin(np.asarray(s.values, dtype=float))) > 0 for s in headline if len(s))
        specs.append(PlotSpecV1(id="losses", title="Losses", question="Does training converge, and does validation follow?",
                                y={"label": "loss", "scale": "log" if pos else "linear"}, source="rules",
                                series=[SeriesRef(run=rid, key=str(s.key), stage=st) for s in headline]))  # fmt: skip
    evals = [s for s in by.get("metric", []) if s.key.split in (Split.VAL, Split.TEST) and not s.key.dims][:6]
    if evals:
        specs.append(PlotSpecV1(id="evaluation", title="Evaluation metrics", question="How do the evaluation metrics move?",
                                source="rules", series=[SeriesRef(run=rid, key=str(s.key), stage=st) for s in evals]))  # fmt: skip
    lr = by.get("lr", [])[:2]
    if lr:
        specs.append(PlotSpecV1(id="lr", title="Learning rate", question="Warm-up, schedule and its end value",
                                source="rules", series=[SeriesRef(run=rid, key=str(s.key), stage=st) for s in lr]))  # fmt: skip
    sig = by.get("sigma", [])[:12]
    if sig:
        specs.append(PlotSpecV1(id="task-weights", title="Effective task weights exp(-2·log σ)",
                                question="Which heads does the learned uncertainty weighting up- or down-weight?",
                                source="rules", y={"label": "effective weight", "scale": "linear"},
                                series=[SeriesRef(run=rid, key=str(s.key), transform="exp_neg2", stage=st) for s in sig]))  # fmt: skip
    return specs


PROMPT = """You choose plots for a training run. Reply ONLY with a JSON array (at most 4 items) of plot specs that
follow this JSON Schema exactly: {schema}

Rules: use only these metric keys (any other key is rejected): {keys}
Use run "{run}" for every series. Prefer few, focused series that answer the question. transform exp_neg2 is only
for task_log_sigma keys. Use a log y scale only for strictly positive losses.

Question: {question}"""


def suggest_ai(container: Any, run: str, question: str) -> dict[str, Any]:
    """The local LLM proposes specs; each passes validation or is dropped with the reason."""
    from model_forensic.application.ports import ChatMessage, is_offline

    svc = container.service
    rid = str(svc.ref(run))
    keys = sorted(_series_map(svc, rid))[:400]
    if is_offline(container.llm):
        return {"model": None, "specs": [], "rejected": [], "fallback": [s.model_dump() for s in suggest(svc, rid)],
                "note": "no LLM connected — rule-based suggestions shown"}  # fmt: skip
    prompt = PROMPT.format(schema=json.dumps(PlotSpecV1.model_json_schema()), keys=", ".join(keys), run=rid,
                           question=question[:500] or "What should I look at first?")  # fmt: skip
    r = container.llm.complete([ChatMessage("user", prompt)])
    text = r.content or ""
    m = re.search(r"\[.*\]", text, re.S)
    try:
        docs = json.loads(m.group(0)) if m else []
    except ValueError:
        docs = []
    ok, rejected = [], []
    for d in docs[:4] if isinstance(docs, list) else []:
        if isinstance(d, dict):
            d = {**d, "source": "ai"}
        spec, errs, _w = validate(d, svc)
        if spec is not None:
            ok.append(spec.model_dump())
        else:
            rejected.append({"spec": d, "errors": errs})
    return {"model": r.model, "specs": ok, "rejected": rejected,
            "note": "" if docs else "the model's reply contained no JSON array"}  # fmt: skip
