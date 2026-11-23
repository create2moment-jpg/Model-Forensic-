"""Expected vs actual: what the training code says the losses should do, checked against what they did.

A local LLM inside the training repository reads the code and the run's resolved config (harness task
``loss_expectations``) and writes an **expectations file** to the knowledge inbox (``<inbox>/expectations/*.yaml``,
schema ``harness/expectations.schema.json``): every loss term with its formula, weight, expected initial value,
range, direction and when it switches on, how the total is composed, and config rules. Model Forensic checks the
logged series against it — deterministically — and says for each mismatch whether it points at the **data**, the
**implementation**, the **config** or the **schedule**.

Expectations are matched to a run by run id (``runs``) or by config (``when_config``: flattened key glob → value
glob, all must match). The newest matching file wins.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from model_forensic.analysis import signal
from model_forensic.domain.metrics import MetricSeries

Hypothesis = Literal["data", "implementation", "config", "schedule", "optimisation"]


class Initial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float = Field(description="expected value at the first logged steps")
    tolerance: float = Field(0.3, ge=0, description="relative tolerance (0.3 = ±30%)")
    why: str = Field("", description="how the value follows from the code, e.g. 'focal loss at init with prior 0.01'")


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = None
    max: float | None = None


class TermExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        description="logged metric key or glob as in the brief, e.g. train/loss/cls or train/loss/cls[layer=*]"
    )
    formula: str = Field(
        "", description="the loss as implemented, e.g. 'sigmoid focal loss, alpha 0.25, gamma 2, / num matched'"
    )
    weight: float | None = Field(None, description="its weight in the total for this config")
    weight_key: str = Field("", description="flattened config key holding the weight")
    in_total: bool = Field(True, description="part of the logged total loss")
    required: bool = Field(True, description="must be logged for this config")
    initial: Initial | None = None
    range: Range | None = None
    direction: Literal["decrease", "increase", "flat", "any"] = "decrease"
    active_from: float | None = Field(None, description="the term switches on at this point (see active_unit)")
    active_unit: Literal["step", "epoch", "fraction"] = "step"
    layers: int | None = Field(None, description="number of decoder layers logged for this term (layer=…)")
    layer_order: Literal["decreasing", "none"] = Field("none", description="decreasing: later layers end lower")
    why: str = ""
    code_refs: list[str] = Field(default_factory=list, description="file:line in the training code")


class TotalExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("train/loss", description="logged key of the total")
    composition: Literal["weighted_sum", "sum", "other"] = "weighted_sum"
    tolerance: float = Field(0.05, ge=0, description="relative residual allowed")
    code_refs: list[str] = Field(default_factory=list)


class ConfigExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(description="flattened config key as in the brief")
    equals: Any = None
    one_of: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    why: str = ""
    code_refs: list[str] = Field(default_factory=list)


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs: list[str] = Field(default_factory=list, description="run ids this file is for")
    when_config: dict[str, str] = Field(default_factory=dict, description="or: flattened config key glob → value glob")
    codebase: str = ""
    generated_by: str = ""
    generated_at: str = ""
    config_source: str = Field("", description="the resolved config file the agent read")
    terms: list[TermExpectation] = Field(default_factory=list)
    total: TotalExpectation | None = None
    config: list[ConfigExpectation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def schema_text() -> str:
    s = Expectations.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = "Model Forensic loss expectations"
    return json.dumps(s, indent=2) + "\n"


def folder(knowledge_folder: Path) -> Path:
    return knowledge_folder / "expectations"


def load_all(knowledge_folder: Path) -> tuple[list[tuple[Path, Expectations]], list[str]]:
    out: list[tuple[Path, Expectations]] = []
    errors: list[str] = []
    d = folder(knowledge_folder)
    if not d.is_dir():
        return out, errors
    for p in sorted(d.glob("*.y*ml")):
        try:
            out.append((p, Expectations.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})))
        except (OSError, yaml.YAMLError, ValidationError) as e:
            errors.append(f"{p.name}: {str(e).splitlines()[0]}")
    return out, errors


def matches(e: Expectations, run_ids: set[str], flat_config: dict[str, Any]) -> bool:
    if e.runs and run_ids & set(e.runs):
        return True
    if not e.when_config:
        return False
    for kg, vg in e.when_config.items():
        if not any(fnmatch.fnmatchcase(k, kg) and fnmatch.fnmatchcase(str(v), vg) for k, v in flat_config.items()):
            return False
    return True


# ── checks ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _keys(pattern: str, series: dict[str, MetricSeries]) -> list[str]:
    if pattern in series:
        return [pattern]
    return sorted(k for k in series if fnmatch.fnmatchcase(k, pattern))


def _first(s: MetricSeries) -> float | None:
    _, v = s.finite()
    return float(np.median(v[: max(1, min(3, v.size))])) if v.size else None


def _end(s: MetricSeries) -> float | None:
    _, v = s.finite()
    return float(signal.ema(v, 0.3)[-1]) if v.size else None


def _active_step(t: TermExpectation, epoch: MetricSeries | None, span: float) -> float | None:
    if t.active_from is None:
        return None
    if t.active_unit == "step":
        return t.active_from
    if t.active_unit == "fraction":
        return t.active_from * span
    if epoch is not None:
        st, v = epoch.finite()
        i = int(np.searchsorted(v, t.active_from))
        return float(st[min(i, st.size - 1)]) if st.size else None
    return None


def _check(cid: str, term: str, kind: str, status: str, text: str, hyp: str, *, expected: Any = None,
           actual: Any = None, keys: list[str] | None = None, refs: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    return {"id": cid, "term": term, "kind": kind, "status": status, "text": text, "hypothesis": hyp,
            "expected": expected, "actual": actual, "keys": keys or [], "code_refs": refs or []}  # fmt: skip


def check(e: Expectations, series: dict[str, MetricSeries], flat_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every expectation against the run: status bad / warn / ok, the evidence and the likely kind of cause."""
    out: list[dict[str, Any]] = []
    span = max((float(np.nanmax(s.arrays()[0])) for s in series.values() if len(s)), default=0.0)
    epoch = series.get("train/epoch")
    covered: set[str] = set()
    for t in e.terms:
        ks = _keys(t.name, series)
        covered.update(ks)
        if not ks:
            if t.required:
                out.append(_check(f"presence:{t.name}", t.name, "presence", "bad",
                                  f"{t.name} should be logged for this config but is not.", "implementation",
                                  expected="logged", actual="missing", refs=t.code_refs))  # fmt: skip
            continue
        out.append(_check(f"presence:{t.name}", t.name, "presence", "ok", f"{t.name} is logged ({len(ks)} series).",
                          "implementation", keys=ks[:12], refs=t.code_refs))  # fmt: skip
        plain = [k for k in ks if "[" not in k] or ks
        s = series[plain[0]]
        if t.initial is not None:
            v0 = _first(s)
            if v0 is not None:
                rel = abs(v0 - t.initial.value) / (abs(t.initial.value) or 1e-12)
                ok = rel <= t.initial.tolerance
                low = v0 < t.initial.value
                why = f" ({t.initial.why})" if t.initial.why else ""
                if ok:
                    tail = "."
                elif low:
                    tail = (" — lower than the code implies: fewer or smaller targets than it assumes (missing labels, a "
                            "filtered class, empty frames) or a larger normaliser.")  # fmt: skip
                else:
                    tail = (" — higher than the code implies: a normaliser or initialisation that differs from the code "
                            "you expect (e.g. a missing bias prior), or targets on a different scale.")  # fmt: skip
                out.append(_check(
                    f"initial:{t.name}", t.name, "initial value", "ok" if ok else "bad",
                    f"{plain[0]} starts at {v0:.4g}; the code implies ≈ {t.initial.value:.4g} ± "
                    f"{t.initial.tolerance:.0%}{why}{tail}",
                    "data" if low else "implementation", expected=t.initial.value, actual=v0,
                    keys=[plain[0]], refs=t.code_refs))  # fmt: skip
        if t.range is not None:
            bad_lo = [
                k
                for k in ks
                if t.range.min is not None and float(np.nanmin(series[k].finite()[1])) < t.range.min - 1e-9
            ]
            bad_hi = [
                k
                for k in ks
                if t.range.max is not None and float(np.nanmax(series[k].finite()[1])) > t.range.max + 1e-9
            ]
            if bad_lo or bad_hi:
                out.append(_check(
                    f"range:{t.name}", t.name, "range", "bad",
                    f"{', '.join((bad_lo + bad_hi)[:4])} leave the possible range [{t.range.min}, {t.range.max}] — "
                    "impossible for this formula: a sign / scale bug or a different loss than the code reads.",
                    "implementation", expected=[t.range.min, t.range.max], keys=(bad_lo + bad_hi)[:6], refs=t.code_refs))  # fmt: skip
        if t.direction in ("decrease", "flat") and len(s) >= 8:
            st, v = s.finite()
            sm = signal.ema(v, 0.2)
            a, b = float(sm[0]), float(sm[-1])
            stop = None
            if t.direction == "decrease" and a > b:
                # where all but 2% of the remaining improvement is made, and the curve stays within 4% after it
                lvl = abs(a) or 1e-12
                k = next((i for i in range(sm.size) if sm[i] - sm[-1] < 0.02 * lvl
                          and float(np.max(np.abs(sm[i:] - sm[-1]))) < 0.04 * lvl), sm.size - 1)  # fmt: skip
                if k < 0.5 * sm.size and sm.size > 10:
                    stop = float(st[k])
            if t.direction == "decrease" and b > a * 1.02:
                out.append(_check(f"direction:{t.name}", t.name, "direction", "bad",
                                  f"{plain[0]} should fall but ends higher than it starts ({a:.4g} → {b:.4g}).",
                                  "optimisation", keys=[plain[0]], refs=t.code_refs))  # fmt: skip
            elif stop is not None and span and stop / span < 0.5:
                out.append(_check(
                    f"direction:{t.name}", t.name, "direction", "warn",
                    f"{plain[0]} stops learning at {stop / span:.0%} of training and stays flat — typical of targets "
                    "that are missing or constant for part of the data (e.g. a label source without this target).",
                    "data", keys=[plain[0]], refs=t.code_refs))  # fmt: skip
            elif t.direction == "flat" and abs(b - a) / (abs(a) or 1e-12) > 0.2:
                out.append(_check(f"direction:{t.name}", t.name, "direction", "warn",
                                  f"{plain[0]} should stay flat but moves {a:.4g} → {b:.4g}.", "implementation",
                                  keys=[plain[0]], refs=t.code_refs))  # fmt: skip
        at = _active_step(t, epoch, span)
        if at is not None and span:
            st, v = s.finite()
            before = v[st < at * 0.98]
            after = v[st > at * 1.02]
            on_early = before.size > 2 and float(np.max(np.abs(before))) > 1e-9
            off_late = after.size > 2 and float(np.max(np.abs(after))) <= 1e-9
            if on_early or off_late:
                out.append(_check(
                    f"active:{t.name}", t.name, "switch-on", "warn",
                    f"{plain[0]} should switch on at step {at:g} but is "
                    f"{'non-zero before it' if on_early else 'still zero after it'}.", "schedule",
                    expected=at, keys=[plain[0]], refs=t.code_refs))  # fmt: skip
        layer_keys = [k for k in ks if re.search(r"layer=\d+", k)]
        if t.layers is not None and layer_keys:
            n = len({re.search(r"layer=(\d+)", k).group(1) for k in layer_keys})  # type: ignore[union-attr]
            if n != t.layers:
                out.append(_check(f"layers:{t.name}", t.name, "layers", "warn",
                                  f"{n} decoder layers logged for {t.name}, the config builds {t.layers}.",
                                  "config", expected=t.layers, actual=n, keys=layer_keys[:12], refs=t.code_refs))  # fmt: skip
        if t.layer_order == "decreasing" and len(layer_keys) >= 3:
            by = sorted((int(re.search(r"layer=(\d+)", k).group(1)), k) for k in layer_keys)  # type: ignore[union-attr]
            ends = [_end(series[k]) or 0.0 for _, k in by]
            if ends[-1] > min(ends) * 1.02:
                out.append(_check(f"order:{t.name}", t.name, "layer order", "warn",
                                  f"The last layer of {t.name} ends {ends[-1] / min(ends) - 1:+.1%} above the best layer: "
                                  "the refinement the code intends does not happen.", "implementation",
                                  keys=[k for _, k in by], refs=t.code_refs))  # fmt: skip
        if t.weight is not None and t.weight_key and t.weight_key in flat_config:
            cv = flat_config[t.weight_key]
            try:
                same = math.isclose(float(cv), t.weight, rel_tol=1e-6)
            except (TypeError, ValueError):
                same = False
            if not same:
                out.append(_check(f"weightkey:{t.name}", t.name, "weight", "bad",
                                  f"The config sets {t.weight_key} = {cv}, the expectation (from the code) says {t.weight}.",
                                  "config", expected=t.weight, actual=cv, refs=t.code_refs))  # fmt: skip
    out += _composition(e, series)
    for c in e.config:
        val = flat_config.get(c.key)
        if val is None:
            out.append(_check(f"config:{c.key}", c.key, "config", "warn", f"{c.key} is not in the run's config.",
                              "config", refs=c.code_refs))  # fmt: skip
            continue
        bad = (c.equals is not None and str(val) != str(c.equals)) or (c.one_of is not None and val not in c.one_of)
        with contextlib.suppress(TypeError, ValueError):
            bad = bad or (c.min is not None and float(val) < c.min) or (c.max is not None and float(val) > c.max)
        exp = c.equals if c.equals is not None else c.one_of if c.one_of is not None else [c.min, c.max]
        out.append(_check(f"config:{c.key}", c.key, "config", "bad" if bad else "ok",
                          f"{c.key} = {val}" + (f"; expected {exp}" if bad else "") + (f" — {c.why}" if c.why else ""),
                          "config", expected=exp, actual=val, refs=c.code_refs))  # fmt: skip
    extra = sorted(k for k in series if k.startswith("train/loss/") and "[" not in k and k not in covered
                   and (not e.total or k != e.total.name))  # fmt: skip
    if extra and e.terms:
        out.append(_check("presence:unexpected", "", "presence", "warn",
                          f"Logged but not in the code's loss for this config: {', '.join(extra[:6])}.",
                          "implementation", keys=extra[:6]))  # fmt: skip
    return out


def _composition(e: Expectations, series: dict[str, MetricSeries]) -> list[dict[str, Any]]:
    """Is the logged total the weighted sum the code says? Fit the weights the curves imply and compare."""
    if e.total is None or e.total.composition == "other" or e.total.name not in series:
        return []
    terms = [
        t for t in e.terms if t.in_total and t.name in series and (t.weight is not None or e.total.composition == "sum")
    ]
    if not terms:
        return []
    tot = series[e.total.name]
    ts, tv = tot.finite()
    cols = []
    for t in terms:
        s, v = series[t.name].finite()
        if s.size < 3:
            return []
        cols.append(np.interp(ts, s, v))
    a = np.vstack(cols).T
    w = np.array([1.0 if e.total.composition == "sum" else float(t.weight or 0) for t in terms])
    pred = a @ w
    resid = float(np.median(np.abs(pred - tv) / np.maximum(np.abs(tv), 1e-12)))
    implied, *_ = np.linalg.lstsq(a, tv, rcond=None)
    names = [t.name.split("/")[-1] for t in terms]
    fit = ", ".join(f"{n} {x:.3g}" for n, x in zip(names, implied, strict=True))
    want = ", ".join(f"{n} {x:.3g}" for n, x in zip(names, w, strict=True))
    if resid <= e.total.tolerance:
        return [_check("composition", e.total.name, "composition", "ok",
                       f"{e.total.name} = Σ weight × term as the code says (residual {resid:.1%}).", "implementation",
                       keys=[e.total.name], refs=e.total.code_refs)]  # fmt: skip
    return [_check(
        "composition", e.total.name, "composition", "bad",
        f"{e.total.name} is not the weighted sum the code describes (residual {resid:.0%}). The curves imply the "
        f"weights {fit}; the code says {want} — a weight that is not applied, a term missing from the total, or a "
        "regulariser added to it.", "implementation", expected=want, actual=fit,
        keys=[e.total.name, *[t.name for t in terms]], refs=e.total.code_refs)]  # fmt: skip


def evaluate(svc: Any, knowledge_folder: Path, run: str) -> dict[str, Any]:
    """The expectations that match ``run`` (newest file) and their checks; or how to produce them."""
    r = svc.get_run(run)
    ids = {str(r.ref), r.ref.run_id, r.name}
    flat = {k: v for k, v in svc.flat_config(r.ref).items()} if hasattr(svc, "flat_config") else {}
    files, errors = load_all(knowledge_folder)
    hits = [(p, e) for p, e in files if matches(e, ids, flat)]
    where = folder(knowledge_folder)
    base: dict[str, Any] = {
        "found": False,
        "where": str(where),
        "brief_command": f"mf harness brief --run {r.ref} --task loss_expectations > brief.md",
        "errors": errors,
    }
    if not hits:
        return base
    p, e = max(hits, key=lambda h: (h[1].generated_at, h[0].stat().st_mtime))
    series = {str(s.key): s for s in svc.series(r.ref)}
    checks = check(e, series, flat)
    order = {"bad": 0, "warn": 1, "ok": 2, "na": 3}
    checks.sort(key=lambda c: order.get(c["status"], 9))
    terms = []
    for t in e.terms:
        ks = _keys(t.name, series)
        terms.append({"name": t.name, "key": next(iter([k for k in ks if "[" not in k] or ks), None), "formula": t.formula,
                      "weight": t.weight, "initial": t.initial.model_dump() if t.initial else None,
                      "range": t.range.model_dump() if t.range else None, "direction": t.direction,
                      "code_refs": t.code_refs, "why": t.why})  # fmt: skip
    return {**base, "found": True, "file": str(p), "codebase": e.codebase, "generated_by": e.generated_by,
            "generated_at": e.generated_at, "config_source": e.config_source, "notes": e.notes,
            "checks": checks, "terms": terms,
            "counts": {s: sum(c["status"] == s for c in checks) for s in ("bad", "warn", "ok")}}  # fmt: skip
