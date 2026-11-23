"""Calibrating the health questions on real runs (design review D6, work order WO-calibrate-questions).

1. ``template``: the questions answered on the runs an operator will review — every problem the tool raised, with the
   measured value and the threshold it was compared with — as a YAML file where only the **verdicts** are left to
   fill (``true`` = a real problem, ``false`` = a false alarm, ``unsure``), plus problems the tool **missed**.
2. ``check``: the filled file is complete and well-formed (the operator's validation).
3. ``propose``: per threshold, the value that separates the false alarms from the real ones (and from the missed
   problems), or a conflict when no value can; never more than the evidence supports.
4. ``apply``: a person accepts the proposal — it goes into ``config/team.local.yaml`` → ``analysis_params``.

The operator judges; it never sets a number. The arithmetic is deterministic and shown.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from model_forensic.analysis import thresholds

Verdict = Literal["true", "false", "unsure", ""]


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str
    question: str
    status: str
    answer: str = ""
    measure: float | None = None
    param: str = ""
    alarm_when: str = ""
    verdict: Verdict = ""
    why: str = ""


class Missed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str
    question: str
    why: str


class CalibrationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = ""
    date: str = ""
    stage: str | None = None
    items: list[Item] = Field(default_factory=list)
    missed: list[Missed] = Field(default_factory=list)


HEADER = """# Calibration of the health questions — fill in `verdict` (and `why`) for every item, nothing else.
#   verdict: true   — a real problem in this run (the alarm is right)
#            false  — a false alarm (the run is fine in this respect); `why` is required
#            unsure — you cannot tell from the run / code; `why` is required
# `missed`: problems the tool did NOT raise, as {run, question, why} (question = one of the ids listed in `questions`).
# Do not change measure / param / status: they are what the tool measured. Validate: mf calibrate check <this file>
"""


def template(svc: Any, runs: Sequence[str], stage: str | None = None, reviewer: str = "") -> str:
    from model_forensic.analysis.loss_questions import ask
    from model_forensic.application.stages import stage_view

    items: list[dict[str, Any]] = []
    ids: set[str] = set()
    for run in runs:
        series = {str(s.key): s for s in stage_view(svc.series(run), stage)}
        for a in ask(series):
            ids.add(a.id)
            if a.status in ("bad", "warn"):
                items.append({"run": run, "question": a.id, "status": a.status, "answer": a.answer,
                              "measure": a.measure, "param": a.param, "alarm_when": a.alarm_when,
                              "verdict": "", "why": ""})  # fmt: skip
    body = {"reviewer": reviewer, "date": date.today().isoformat(), "stage": stage, "items": items, "missed": []}
    text = HEADER + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=120)
    return text + "# questions: " + ", ".join(sorted(ids)) + "\n"


def parse(text: str) -> CalibrationFile:
    return CalibrationFile.model_validate(yaml.safe_load(text) or {})


def check(file: Path) -> list[str]:
    """What is missing or wrong in a filled calibration file ([] = complete)."""
    from model_forensic.analysis.loss_questions import ask

    try:
        cal = parse(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValidationError) as e:
        return [str(e).splitlines()[0]]
    known = {x.id for x in ask({})} | {"base_total", "base_terms", "loss_vs_eval"}  # baseline-only questions
    out: list[str] = []
    for i, it in enumerate(cal.items, 1):
        if it.verdict == "":
            out.append(f"item {i} ({it.run} · {it.question}): verdict missing — true / false / unsure")
        elif it.verdict in ("false", "unsure") and not it.why.strip():
            out.append(f"item {i} ({it.run} · {it.question}): `why` is required for {it.verdict}")
    for j, m in enumerate(cal.missed, 1):
        if m.question not in known:
            out.append(f"missed {j}: unknown question {m.question!r} (use an id from the `questions` line)")
        if not m.why.strip():
            out.append(f"missed {j}: `why` is required")
    if not cal.items and not cal.missed:
        out.append("no items and no missed problems — nothing to calibrate")
    return out


def _missed_measures(svc: Any, missed: Iterable[Missed], stage: str | None) -> list[tuple[str, float, str]]:
    """(param, measure, alarm_when) for each missed problem — the question re-asked on its run."""
    from model_forensic.analysis.loss_questions import ask
    from model_forensic.application.stages import stage_view

    out: list[tuple[str, float, str]] = []
    cache: dict[str, dict[str, Any]] = {}
    for m in missed:
        if m.run not in cache:
            series = {str(s.key): s for s in stage_view(svc.series(m.run), stage)}
            cache[m.run] = {a.id: a for a in ask(series)}
        a = cache[m.run].get(m.question)
        if a is not None and a.param and a.measure is not None:
            out.append((a.param, a.measure, a.alarm_when))
    return out


def propose(files: Sequence[Path], svc: Any = None, min_verdicts: int = 2) -> dict[str, Any]:
    """Per threshold: the evidence (true / false alarms, missed problems) and a proposed value — or why none."""
    trues: dict[str, list[float]] = {}
    falses: dict[str, list[float]] = {}
    direction: dict[str, str] = {}
    for f in files:
        cal = parse(f.read_text(encoding="utf-8"))
        for it in cal.items:
            if not it.param or it.measure is None or it.verdict not in ("true", "false"):
                continue
            direction[it.param] = it.alarm_when or "above"
            (trues if it.verdict == "true" else falses).setdefault(it.param, []).append(it.measure)
        if svc is not None and cal.missed:
            for param, measure, when in _missed_measures(svc, cal.missed, cal.stage):
                direction[param] = when or "above"
                trues.setdefault(param, []).append(measure)
    rows: list[dict[str, Any]] = []
    params: dict[str, dict[str, float]] = {}
    for param in sorted(set(trues) | set(falses)):
        section, key = param.split(".", 1)
        cur = thresholds.t(section, key)
        tv, fv = trues.get(param, []), falses.get(param, [])
        up = direction.get(param, "above") == "above"
        row: dict[str, Any] = {"param": param, "current": cur, "alarm_when": "above" if up else "below",
                               "true": sorted(tv), "false": sorted(fv), "proposed": None, "reason": ""}  # fmt: skip
        if len(tv) + len(fv) < min_verdicts:
            row["reason"] = f"only {len(tv) + len(fv)} verdict(s) — at least {min_verdicts} needed"
        elif not fv and all((x > cur) if up else (x < cur) for x in tv):
            row["reason"] = "no false alarms and every real problem is caught — keep"
        else:
            # alarm when above: the threshold must lie above every false alarm and at or below every real problem;
            # alarm when below: the mirror image
            lo: float | None = (max(fv) if fv else None) if up else (max(tv) if tv else None)
            hi: float | None = (min(tv) if tv else None) if up else (min(fv) if fv else None)
            if lo is not None and hi is not None and lo >= hi:
                row["reason"] = (f"cannot separate: a real problem at {hi if up else lo:.4g} and a false alarm at "
                                 f"{lo if up else hi:.4g} — the question needs another signal, not another threshold "
                                 "(escalate)")  # fmt: skip
            else:
                if lo is not None and hi is not None:
                    new = (lo + hi) / 2
                elif lo is not None:  # only the lower side known: just above it
                    new = lo * 1.1 if lo > 0 else lo + 1e-6
                elif hi is not None:  # only the upper side known: just below it
                    new = hi * 0.9 if hi > 0 else hi - 1e-6
                else:
                    new = cur
                new = float(f"{new:.4g}")
                if math.isclose(new, cur, rel_tol=0.02):
                    row["reason"] = "the evidence agrees with the current value — keep"
                else:
                    row["proposed"] = new
                    row["reason"] = (f"between {lo:.4g} and {hi:.4g}" if lo is not None and hi is not None
                                     else f"just past {lo if lo is not None else hi:.4g}")  # fmt: skip
                    params.setdefault(section, {})[key] = new
        rows.append(row)
    return {"rows": rows, "analysis_params": params}


def proposal_yaml(p: dict[str, Any]) -> str:
    lines = ["# Proposed thresholds from calibration verdicts — review, then: mf calibrate apply <this file>", "#"]
    for r in p["rows"]:
        lines.append(f"# {r['param']}: {r['current']:g} → {r['proposed'] if r['proposed'] is not None else 'keep'} "
                     f"({r['reason']}; real {r['true']}, false {r['false']})")  # fmt: skip
    return "\n".join(lines) + "\n" + yaml.safe_dump({"analysis_params": p["analysis_params"]}, sort_keys=False)


def apply(proposal: Path, team_file: Path) -> dict[str, dict[str, float]]:
    """Merge the proposal's ``analysis_params`` into the team policy file (a ``.bak`` copy is kept)."""
    new = (yaml.safe_load(proposal.read_text(encoding="utf-8")) or {}).get("analysis_params") or {}
    doc: dict[str, Any] = {}
    if team_file.exists():
        doc = yaml.safe_load(team_file.read_text(encoding="utf-8")) or {}
        shutil.copyfile(team_file, team_file.with_suffix(team_file.suffix + ".bak"))
    params = doc.setdefault("analysis_params", {})
    for section, values in new.items():
        params.setdefault(section, {}).update(values)
    team_file.parent.mkdir(parents=True, exist_ok=True)
    team_file.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {s: dict(v) for s, v in new.items()}
