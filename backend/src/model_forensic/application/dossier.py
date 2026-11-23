"""The analysis dossier: everything Model Forensic found about a run, as one document for an interpreting LLM.

Like a junior doctor's case file for the consultant: the observations and test results, each with an **evidence
id** (``[E-Q3]``), facts kept apart from hypotheses, and what could not be examined — then the instructions for the
reader (``defaults/dossier_prompt.md``). The interpretation happens outside the tool (a local LLM, ideally inside the
training repository so it can also read the code).

Deterministic: every number comes from the analyses (health questions, loss drift, expectations from the codebase,
model internals, comparison, stored findings, team knowledge, the ledger). One stage at a time — pretrain, finetune
and evaluation are never mixed. Secrets and paths are left out of the configuration.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from model_forensic import __version__
from model_forensic.analysis.config_diff import flatten

PROMPT = Path(__file__).resolve().parents[1] / "defaults" / "dossier_prompt.md"
_SKIP = ("output_dir", "root_dir", "log_dir", "cache", "tmp", "password", "token", "secret", "sas", "key_", "credential",
         "path", "dir", "url", "host", "user", "subscription", "tenant", "workspace", "resource_group")  # fmt: skip
LIMITS = {"brief": {"config": 60, "metrics": 20, "losses": 15, "drift": 8}, "standard": {"config": 150, "metrics": 40,
          "losses": 40, "drift": 20}, "full": {"config": 400, "metrics": 120, "losses": 120, "drift": 60}}  # fmt: skip


def _n(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return "—" if not math.isfinite(x) else f"{x:.4g}"
    return str(x)


def _pct(x: float | None) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:+.1%}"


class _Doc:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.count: dict[str, int] = {}

    def eid(self, prefix: str) -> str:
        self.count[prefix] = self.count.get(prefix, 0) + 1
        return f"E-{prefix}{self.count[prefix]}"

    def h(self, text: str) -> None:
        self.lines += ["", f"## {text}", ""]

    def p(self, text: str) -> None:
        self.lines.append(text)

    def item(self, prefix: str, text: str) -> None:
        self.lines.append(f"- [{self.eid(prefix)}] {text}")


def build(
    svc: Any,
    knowledge: Any,
    docs: Any,
    run: str,
    baseline: str | None = None,
    stage: str | None = None,
    detail: str = "standard",
    with_prompt: bool = True,
) -> str:
    from model_forensic.analysis.internals import internals
    from model_forensic.analysis.loss_drift import loss_drift
    from model_forensic.analysis.loss_questions import ask
    from model_forensic.analysis.series_ops import is_loss as _is_loss
    from model_forensic.analysis.series_ops import is_val as _is_val
    from model_forensic.analysis.series_ops import reach as _reach
    from model_forensic.application.expectations import evaluate
    from model_forensic.application.stages import stage_view

    lim = LIMITS.get(detail, LIMITS["standard"])
    d = _Doc()
    r = svc.get_run(run)
    stages = svc.stages(r.ref)
    stage_names = [n for n, _ in stages]
    if stage and stage not in stage_names:
        raise ValueError(f"{run} has no stage {stage!r} (stages: {', '.join(stage_names) or 'none'})")
    b = svc.get_run(baseline) if baseline else None
    series = {str(s.key): s for s in stage_view(svc.series(r.ref), stage)}
    b_stage = stage if (b and stage and stage in [n for n, _ in svc.stages(b.ref)]) else None
    bseries = {str(s.key): s for s in stage_view(svc.series(b.ref), b_stage)} if b else None
    missing: list[str] = []

    name = r.display_name or r.name
    d.p(f"# Analysis dossier — {name}")
    d.p("")
    d.p(f"Prepared by Model Forensic {__version__} on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
        f"Run `{r.ref}`" + (f", compared with the baseline `{b.ref}`" if b else "")
        + f"; metrics read from **{stage or 'each metric’s last stage'}** (stages are never mixed). "
        "Every observation carries an evidence id — cite it. Lines marked *hypothesis* are the tool's reading, not "
        "facts. Numbers are smoothed end values unless stated.")  # fmt: skip

    # 1. The run
    d.h("1. The run")
    d.item("R", f"{name} — kind {r.kind.value}, status **{r.status.value}**, experiment {r.experiment or '—'}")
    if r.started_at and r.ended_at:
        d.item("R", f"duration {(r.ended_at - r.started_at).total_seconds() / 3600:.1f} h")
    comp = r.compute
    gpu = " ".join(str(x) for x in (getattr(comp, "instance_count", None), getattr(comp, "instance_type", None)) if x)
    if gpu:
        d.item("R", f"compute {gpu}")
    if r.git and (r.git.branch or r.git.commit):
        d.item("R", f"code: branch {r.git.branch or '—'}, commit {(r.git.commit or '—')[:12]}")
    if r.datasets:
        d.item("R", "datasets: " + ", ".join(ds.name for ds in r.datasets[:10]))
    if stage_names:
        d.item("R", "pipeline stages (order): " + ", ".join(stage_names))
    if r.failure:
        d.item("R", f"failure: {getattr(r.failure, 'message', '') or r.failure}")
    if b:
        d.item("R", f"baseline: {b.display_name or b.name} (`{b.ref}`), status {b.status.value}")

    # 2. Configuration
    d.h("2. Configuration")
    if b:
        diff = svc.diff_configs(b.ref, r.ref)
        meaningful = list(diff.meaningful)
        d.p(f"{len(meaningful)} meaningful differences to the baseline ({len(diff.changes) - len(meaningful)} "
            "runtime-only left out):")  # fmt: skip
        for c in meaningful[: lim["config"]]:
            d.item("C", f"{c.category}: {c.describe()}")
        if not meaningful:
            d.p("- no meaningful configuration difference — suspect code, data or seed")
    else:
        flat = flatten({c.source: c.data for c in svc.configs(r.ref) if c.source != "params"})
        kept = [(k, v) for k, v in sorted(flat.items()) if not any(p in k.lower() for p in _SKIP)]
        for k, v in kept[: lim["config"]]:
            d.item("C", f"`{k}` = `{_n(v) if not isinstance(v, str) else v[:80]}`")
        if len(kept) > lim["config"]:
            d.p(f"- … {len(kept) - lim['config']} more keys (use `--detail full`)")

    # 3. Results
    d.h("3. Results (evaluation metrics)")
    vals = sorted(k for k in series if _is_val(k) and not _is_loss(k))
    if b:
        try:
            cmp = svc.compare(b.ref, r.ref)
            basis = "baseline seed replicates" if cmp.baseline_replicates else "a fixed threshold (single seed)"
            d.p(f"Significance from {basis}.")
            moved = sorted(cmp.deltas, key=lambda x: -abs(x.relative or 0))[: lim["metrics"]]
            for x in moved:
                d.item("M", f"{x.metric}: {_n(x.baseline)} → {_n(x.candidate)} ({_pct(x.relative)}), **{x.verdict.value}**"
                       + (f", z {x.z_score:.1f}" if x.z_score is not None else ""))  # fmt: skip
        except Exception as e:
            missing.append(f"comparison with the baseline: {e}")
    else:
        for k in vals[: lim["metrics"]]:
            _, v = series[k].finite()
            if v.size:
                d.item("M", f"{k}: {_n(float(v[-1]))}" + (f" (best {_n(float(v.max()))})" if v.size > 1 else ""))
    if not vals and not b:
        missing.append("evaluation metrics (none logged in this stage)")

    # 4. Loss trajectories
    d.h("4. Loss trajectories")
    losses = sorted(k for k in series if _is_loss(k))
    d.p("start → minimum (where) → end; t90 = share of the schedule at which 90% of the drop was made.")
    for k in losses[: lim["losses"]]:
        st, v = series[k].finite()
        if v.size < 2:
            continue
        i = int(np.argmin(v))
        span = float(st[-1] - st[0]) or 1.0
        t90 = _reach(series[k])["t90"]
        d.item("L", f"{k}: {_n(float(v[0]))} → min {_n(float(v[i]))} at {(st[i] - st[0]) / span:.0%} → end "
               f"{_n(float(v[-1]))}" + (f"; t90 {t90:.0%}" if t90 is not None else ""))  # fmt: skip
    if not losses:
        missing.append("training losses (none logged in this stage)")

    # 5. Health questions
    d.h("5. Training health questions")
    for a in ask(series, bseries):
        mark = {"bad": "PROBLEM", "warn": "look", "ok": "ok", "na": "not logged"}[a.status]
        d.item("Q", f"**{a.question}** — {mark}: {a.answer}")

    # 6. Drift
    if b and bseries is not None:
        d.h("6. Loss drift against the baseline")
        dr = loss_drift(
            bseries, series, ref_lr=None, cand_lr=None, changes=tuple(svc.diff_configs(b.ref, r.ref).changes)
        )
        d.p(dr["summary"] + f" (aligned by {dr['aligned_by']})")
        for x in dr["readings"]:
            d.item("D", f"*hypothesis*: {x}")
        for row in [x for x in dr["rows"] if x["outcome"] not in ("same", "insufficient")][: lim["drift"]]:
            d.item("D", f"{row['key']}: {row['outcome']} from {row['diverge_fraction']:.0%} of training, end gap "
                   f"{_pct(row['final_gap'])}" + (f"; {'; '.join(row['shape_notes'])}" if row["shape_notes"] else ""))  # fmt: skip

    # 7. Codebase expectations
    d.h("7. Against the codebase (expected vs actual losses)")
    ex = evaluate(svc, knowledge.folder, str(r.ref), series=series)
    if ex["found"]:
        d.p(f"Expectations from {ex.get('codebase') or 'the codebase'} ({ex.get('generated_by') or '—'}, "
            f"{ex.get('generated_at') or '—'}): {ex['counts']['bad']} mismatches, {ex['counts']['warn']} to look at.")  # fmt: skip
        for c in ex["checks"]:
            if c["status"] == "ok" and detail == "brief":
                continue
            refs = f" (code: {', '.join(c['code_refs'][:2])})" if c["code_refs"] else ""
            d.item("X", f"{c['status'].upper()} · {c['kind']} · points at *{c['hypothesis']}*: {c['text']}{refs}")
    else:
        missing.append("expectations from the codebase (run the harness task loss_expectations)")
        d.p("Not available for this run.")

    # 8. Internals
    it = internals(series, bseries)
    if it["logged"]:
        d.h("8. Model internals (decoder cross-attention, queries)")
        a = it["attention"]
        for la in a["layers"]:
            cells = ", ".join(f"{mo} {a['mass'][la][mo]['end']:.0%}" for mo in a["modalities"]
                              if mo in a["mass"].get(la, {}) and a["mass"][la][mo]["end"] is not None)  # fmt: skip
            ent = a["entropy"].get(la)
            d.item(
                "I",
                f"layer {la}: attention share {cells}" + (f"; focus (entropy) {ent:.2f}" if ent is not None else ""),
            )
        for q in it["queries"]:
            d.item("I", f"{q['key']}: {_n(q['start'])} (first quarter) → {_n(q['end'])}")
        for n in it["notes"]:
            d.item("I", f"*hypothesis*: {n['text']} Check: {n['check']}")
    else:
        missing.append("model internals (attention per layer × modality — instrumentation kit)")

    # 9. Findings
    found = svc.findings.for_runs([r.ref]) if hasattr(svc, "findings") else []
    if found:
        d.h("9. Findings of the deterministic analyses")
        for f in found[:30]:
            d.item("F", f"[{f.severity.value} · {f.confidence.value} · {f.label.value}] **{f.title}** — {f.statement}")

    # 10. Knowledge
    try:
        from model_forensic.application.knowledge import applied, combined

        kn = combined(applied(svc, knowledge, str(r.ref)))
        if kn.entries:
            d.h("10. Team knowledge that applies to this run")
            for ent in kn.entries[:30]:
                d.item("K", f"{ent.kind} {ent.id}: {ent.statement}" + (f" (why: {ent.why})" if ent.why else ""))
    except Exception as e:  # the dossier must not fail on knowledge problems
        missing.append(f"team knowledge: {e}")

    # 11. History
    if docs is not None:
        try:
            from model_forensic.application.ledger import LedgerService

            items, _ = LedgerService(docs, svc).find(text=str(r.ref).split(":")[-1], limit=10)
            if items:
                d.h("11. Experiment ledger")
                for le in items:
                    d.item("H", f"{le['id']} {le['title']} — {le['status']}" + (f", decision **{le['decision']}**" if le.get("decision") else "")
                           + (f" · {le['headline']}" if le.get("headline") else ""))  # fmt: skip
        except Exception as e:
            missing.append(f"ledger: {e}")

    d.h("12. What could not be examined")
    if not b:
        missing.append("a baseline (comparison, drift) — pass one to compare")
    missing.append("seed noise: single seed, so small deltas may be noise" if not (b and _has_reps(svc, b)) else "")
    for m in [m for m in missing if m]:
        d.p(f"- {m}")

    text = "\n".join(d.lines).rstrip() + "\n"
    if with_prompt and PROMPT.is_file():
        text += "\n---\n\n" + PROMPT.read_text(encoding="utf-8").strip() + "\n"
    return text


def _has_reps(svc: Any, b: Any) -> bool:
    try:
        return bool(svc.replicates(b.ref))
    except Exception:
        return False


def save(text: str, knowledge_folder: Path, run: str, out: Path | None = None) -> Path:
    """Write the dossier (default: ``<inbox>/dossiers/<run>-<yyyymmdd-hhmm>.md``)."""
    if out is None:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", run).strip("-")
        out = knowledge_folder / "dossiers" / f"{slug}-{datetime.now(timezone.utc):%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
