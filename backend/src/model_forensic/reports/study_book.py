"""Study book: run ids in, a multi-chapter forensic book out — deterministic, every number from an artefact.

Mechanises the structure of a hand-written training study: the verdict up front, the cast, the pitfall checks
(P1–P8), the ruler-free per-class comparison, convergence read with the learning rate, the loss ledger, what each
pipeline stage changed, where the model fails, the study's hypotheses, next steps, and the evidence sources.
Optional: a grounded AI narrative (labelled AI-generated) when an LLM is configured.
"""

from __future__ import annotations

import html
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from model_forensic.analysis import shared_ruler as sr
from model_forensic.analysis import study_checks as sc
from model_forensic.analysis.base import AnalysisResult
from model_forensic.analysis.evaljson import GLOSSARY, EvalEvidence
from model_forensic.application.service import ForensicService
from model_forensic.domain.findings import Finding
from model_forensic.domain.run import Run, RunRef
from model_forensic.domain.study import Study
from model_forensic.reports import svgplot

ANALYSES = (
    "study_checks",
    "loss_ledger",
    "eval_localization",
    "stage_delta",
    "sequence_drilldown",
    "task_weighting",
    "decoder_layers",
    "metric_coverage",
    "training_dynamics",
    "regression",
)
FAMILY_LABEL = {
    "decoder_cls": "Decoder · class",
    "decoder_bbox": "Decoder · box",
    "denoise_cls": "Denoising · class",
    "denoise_bbox": "Denoising · box",
    "camera": "Camera head",
    "radar": "Radar head",
    "other": "Other",
}
SHORT = {
    "decoder_cls": "Dec·cls",
    "decoder_bbox": "Dec·box",
    "denoise_cls": "DN·cls",
    "denoise_bbox": "DN·box",
    "camera": "Camera",
    "radar": "Radar",
    "other": "Other",
}
PART_ORDER = ("A · Forensics", "B · The loss ledger", "C · Takeaways")
e = html.escape


@dataclass
class RunEvidence:
    label: str
    run: Run
    role: str
    stages: list[str]
    series: dict[Any, Any]
    evals: list[EvalEvidence]
    config: dict[str, Any]
    lr: Any
    results: dict[str, AnalysisResult]
    checks: list[sc.CheckResult]
    ledger: list[sc.LedgerRow]

    @property
    def headline(self) -> float | None:
        ev = sr.last_eval(self.evals)
        if ev is not None and "combined_metric" in ev.combined:
            return ev.combined["combined_metric"]
        for k, s in self.series.items():
            if k.name == "combined_metric" and len(s):
                return float(s.values[-1])
        return None

    @property
    def ruler(self) -> str | None:
        vals = [v for v in sc.config_values(self.config, "combined_metrics_yaml").values() if isinstance(v, str)]
        return sc.ruler_identity(vals[0]) if vals else None

    def findings(self) -> list[Finding]:
        return [f for r in self.results.values() for f in r.findings]


@dataclass
class Chapter:
    id: str
    title: str
    part: str
    body: list[str] = field(default_factory=list)


def gather(svc: ForensicService, study: Study, progress: Callable[[str], None] | None = None) -> list[RunEvidence]:
    say = progress or (lambda _m: None)
    runs = list(dict.fromkeys([*([study.baseline] if study.baseline else []), *study.runs]))
    out = []
    for ref in runs:
        run = svc.get_run(ref)
        label = run.display_name or run.name
        say(f"collecting evidence for {label}")
        base = study.baseline if study.baseline and ref != study.baseline else None
        results = {r.analysis: r for r in svc.analyze(ref, base, analyses=list(ANALYSES), persist=False)}
        rd = svc._safe_run_data(svc.ref(ref), with_evals=True, with_config=True, with_lr=True)
        from model_forensic.analysis.base import AnalysisContext
        from model_forensic.analysis.plugins.study import run_checks

        checks = run_checks(AnalysisContext(target=rd, registry=svc.metrics))
        out.append(
            RunEvidence(
                label=label,
                run=run,
                role="baseline" if ref == study.baseline else "candidate",
                stages=[n for n, _ in svc.stages(ref)],
                series=dict(rd.series),
                evals=list(rd.evals),
                config=dict(rd.configs),
                lr=rd.lr,
                results=results,
                checks=checks,
                ledger=sc.ledger(rd.series),
            )
        )
    return out


# --------------------------------------------------------------------------------------------- html helpers
def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], cls: str = "") -> str:
    def cell(v: Any) -> str:
        if isinstance(v, float):
            return f'<td class="num">{"—" if not math.isfinite(v) else f"{v:.4g}"}</td>'
        if isinstance(v, tuple) and len(v) == 2 and v[0] == "html":
            return f"<td>{v[1]}</td>"
        return f"<td>{e(str(v)) if v is not None else '—'}</td>"

    return (
        f'<div class="tw"><table class="{cls}"><thead><tr>'
        + "".join(f"<th>{e(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(cell(v) for v in r) + "</tr>" for r in rows)
        + "</tbody></table></div>"
    )


def box(kind: str, text_html: str) -> str:
    return f'<div class="{kind}">{text_html}</div>'


def status_pill(s: str) -> tuple[str, str]:
    return ("html", f'<span class="pill {s.lower()}">{e(s)}</span>')


def finding_html(f: Finding, run_label: str | None = None) -> str:
    ev = "".join(f"<li>{e(x.statement)}</li>" for x in f.evidence[:4])
    hy = "".join(f"<li>{e(h.statement)}</li>" for h in f.hypotheses[:3])
    rec = "".join(f"<li>{e(r.title)} — <i>{e(r.expected_outcome)}</i></li>" for r in f.recommendations[:2])
    who = f'<span class="who">{e(run_label)}</span> ' if run_label else ""
    return (
        f'<div class="finding sev-{f.severity.value}"><div class="ft">{who}<b>{e(f.title)}</b> '
        f'<span class="pill">{e(f.severity.value)}</span> <span class="pill ghost">{e(f.label.value)}</span></div>'
        f"<p>{e(f.statement)}</p>"
        + (f"<div class='lbl'>Evidence</div><ul>{ev}</ul>" if ev else "")
        + (f"<div class='lbl'>Hypotheses — not established</div><ul>{hy}</ul>" if hy else "")
        + (f"<div class='lbl'>Next experiment</div><ul>{rec}</ul>" if rec else "")
        + "</div>"
    )


def _frac_series(s: Any) -> tuple[list[float], list[float]]:
    xs, ys = s.finite()
    if not len(xs):
        return [], []
    x0, x1 = float(xs.min()), float(xs.max()) or 1.0
    return [float((x - x0) / ((x1 - x0) or 1)) for x in xs], [float(y) for y in ys]


# --------------------------------------------------------------------------------------------- verdict
def verdict(evid: list[RunEvidence]) -> tuple[str, list[str]]:
    """A deterministic verdict from checks + shared ruler + the most severe findings."""
    lines: list[str] = []
    base = next((r for r in evid if r.role == "baseline"), None)
    cands = [r for r in evid if r.role == "candidate"]
    rulers = {r.label: r.ruler for r in evid if r.ruler}
    headline = ""
    if base and cands and len(set(rulers.values())) > 1:
        evs = {r.label: ev for r in evid if (ev := sr.last_eval(r.evals)) is not None}
        if base.label in evs and len(evs) > 1:
            wins = sr.shared_table(evs).wins(base.label)
            fr = [f"{lab}: better in {b}/{t} class×metric cells" for lab, (b, t) in wins.items() if t]
            hb = {r.label: r.headline for r in evid if r.headline is not None}
            ahead = [lab for lab, (b, t) in wins.items() if t and b / t >= 0.5]
            if hb and ahead:
                headline = (
                    f"The headline gap is a ruler artifact: the runs are scored with different combined-metrics "
                    f"rulers ({', '.join(sorted(set(rulers.values())))}), so {', '.join(f'{k} {v:.3f}' for k, v in hb.items())} "
                    f"are not comparable. On the shared, ruler-free per-class metrics {', '.join(ahead)} "
                    f"{'is' if len(ahead) == 1 else 'are'} ahead of the baseline."
                )
            lines += fr
    fails = [(r.label, c) for r in evid for c in r.checks if c.status == "FAIL"]
    if not headline:
        sev = sorted((f for r in evid for f in r.findings()), key=lambda f: -f.severity.rank)
        headline = (
            f"{len(fails)} pitfall check(s) fail and {sum(1 for f in sev if f.severity.rank >= 3)} high-severity "
            "finding(s) — read them before any conclusion."
            if fails or sev
            else "No failing checks and no high-severity findings."
        )
    for lab, c in fails:
        lines.append(f"{c.id} {c.title} — FAIL on {lab}: {c.detail}")
    seen = set()
    for r in evid:
        for f in sorted(r.findings(), key=lambda f: -f.severity.rank)[:6]:
            if f.severity.rank >= 2 and f.title not in seen and not f.title.startswith("P"):
                seen.add(f.title)
                lines.append(f"{f.title} ({r.label})")
    return headline, lines[:14]


# --------------------------------------------------------------------------------------------- chapters
def chapters(study: Study, evid: list[RunEvidence], ai_text: str | None = None) -> list[Chapter]:
    out: list[Chapter] = []
    head, lines = verdict(evid)

    pre = Chapter("preface", "Preface — the case file", "A · Forensics")
    if study.goal:
        pre.body.append(f"<p>{e(study.goal)}</p>")
    pre.body.append(
        "<h3>The cast</h3>"
        + table(
            ["Run", "Role", "Stages", "Status", "User", "Ruler", "Headline", "Created"],
            [
                [
                    r.label,
                    r.role,
                    " → ".join(r.stages) or "single",
                    r.run.status.value,
                    r.run.user,
                    r.ruler,
                    r.headline if r.headline is not None else "—",
                    r.run.created_at.date().isoformat() if r.run.created_at else "",
                ]
                for r in evid
            ],
        )
    )
    pre.body.append("<h3>The verdict, up front</h3>" + box("verdict", e(head)))
    if lines:
        pre.body.append("<ol>" + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ol>")
    if ai_text:
        pre.body.append(
            "<h3>AI narrative <span class='pill ghost'>AI-generated, grounded in the evidence below</span></h3>"
            + box("ai", e(ai_text).replace("\n", "<br>"))
        )
    out.append(pre)

    ch = Chapter("checks", "Pitfall checks (P1–P8)", "A · Forensics")
    ch.body.append(
        box(
            "hypo",
            "Eight traps a study falls into, each checked mechanically against artefacts that "
            "already exist. <b>Measurement</b> traps (P1–P3, P6) invalidate conclusions; <b>harness</b> traps "
            "(P4, P5) mean the pipeline does not deliver what it promises; <b>interpretation</b> traps (P7, P8) "
            "are about reading.",
        )
    )
    multi = sc.check_ruler({r.label: r.config for r in evid})
    rows = [[multi.id, multi.title, status_pill(multi.status), "all runs", multi.detail]]
    for r in evid:
        rows += [[c.id, c.title, status_pill(c.status), r.label, c.detail] for c in r.checks if c.id != "P1"]
    ch.body.append(table(["#", "Check", "Status", "Run", "Detail"], rows, "checks"))
    out.append(ch)

    evs = {
        r.label: ev
        for r in evid
        if (ev := sr.last_eval(r.evals)) is not None and any(c.metrics for c in ev.classes.values())
    }
    if evs:
        c = Chapter("shared-ruler", "The shared ruler — per-class, ruler-free", "A · Forensics")
        c.body.append(
            box(
                "hypo",
                "<code>torch_metric_evaluation</code> is written for every run whatever its combined-"
                "metrics ruler: the one scoreboard comparable across rulers. Best value per row is marked.",
            )
        )
        t = sr.shared_table(evs)
        c.body.append(
            table(
                ["Class", "Metric", *t.runs],
                [
                    [
                        cell.cls,
                        cell.metric,
                        *[
                            ("html", (f"<b>{cell.values[r]:.4g}</b>" if cell.best == r else f"{cell.values[r]:.4g}"))
                            if r in cell.values
                            else "—"
                            for r in t.runs
                        ],
                    ]
                    for cell in t.cells
                ],
            )
        )
        base = next((r.label for r in evid if r.role == "baseline"), None)
        if base in evs:
            c.body.append(
                "<ul>"
                + "".join(
                    f"<li>{e(k)}: better than {e(base)} in {b} of {n} cells</li>"
                    for k, (b, n) in t.wins(base).items()
                    if n
                )
                + "</ul>"
            )
        c.body.append(
            "<p class='cap'>Glossary: " + " · ".join(f"<b>{e(k)}</b> {e(v)}" for k, v in GLOSSARY.items()) + "</p>"
        )
        out.append(c)

    cv = Chapter("convergence", "Convergence — the loss read with the learning rate", "A · Forensics")
    loss_series = [(r.label, *_frac_series(s)) for r in evid if (s := _find(r, "loss", "train")) is not None]
    if loss_series:
        cv.body.append(svgplot.line_chart(loss_series, "Total training loss", "fraction of training", "loss"))
    lr_series = [(r.label, *_frac_series(r.lr)) for r in evid if r.lr is not None]
    if lr_series:
        cv.body.append(svgplot.line_chart(lr_series, "Learning rate", "fraction of training", "lr"))
    cv.body.append(
        table(
            ["Run", "Plateau verdict (P8)"],
            [[r.label, next((c.detail for c in r.checks if c.id == "P8"), "—")] for r in evid],
        )
    )
    cv.body.append(
        box(
            "keybox",
            "<b>Plateau triage.</b> Flat loss + high LR = converged (change data, weights or capacity); "
            "flat + decayed LR = schedule-bound (extend / re-warm); still descending = learning; total falling while "
            "the network-facing loss is flat = only σ is moving (thermostat).",
        )
    )
    for r in evid:
        for name in ("training_dynamics", "task_weighting", "decoder_layers"):
            res = r.results.get(name)
            if res is not None:
                cv.body += [finding_html(f, r.label) for f in res.findings]
    out.append(cv)

    if any(r.ledger for r in evid):
        lg = Chapter("ledger", "The loss ledger — budget, trust, gradient", "B · The loss ledger")
        lg.body.append(
            box(
                "hypo",
                "A Kendall-weighted loss contributes <code>raw·exp(−2σ)·w + w·σ</code> per subloss. "
                "<b>σ is trust, not share</b>: a large weighted share can be pure magnitude. The network-facing "
                "number is <code>weighted_loss_no_reg</code>; a negative weighted loss is the σ tax, not a bug.",
            )
        )
        fams = [f for f in FAMILY_LABEL if any(x.family == f for r in evid for x in r.ledger)]
        roll = {r.label: sc.family_rollup(r.ledger) for r in evid if r.ledger}
        lg.body.append(
            svgplot.bar_chart(
                [SHORT[f] for f in fams],
                [(lab, [roll[lab].get(f, {}).get("weighted_share", math.nan) for f in fams]) for lab in roll],
                "Share of the weighted total per family",
                "share",
                percent=True,
            )
        )
        for lab, fam in roll.items():
            lg.body.append(
                f"<h3>{e(lab)}</h3>"
                + svgplot.bar_chart(
                    [SHORT[f] for f in fams if f in fam],
                    [
                        (n, [fam[f][k] for f in fams if f in fam])
                        for n, k in (("raw", "raw_share"), ("weighted", "weighted_share"), ("gradient", "no_reg_share"))
                    ],
                    f"{lab}: raw vs weighted vs gradient share",
                    "share",
                    percent=True,
                )
            )
            lg.body.append(
                table(
                    ["Family", "raw Σ", "weighted Σ", "no_reg Σ", "σ tax", "σ min", "σ max"],
                    [
                        [
                            FAMILY_LABEL[f],
                            v["raw"],
                            v["weighted"],
                            v["no_reg"],
                            v["tax"],
                            v["sigma_min"],
                            v["sigma_max"],
                        ]
                        for f, v in fam.items()
                    ],
                )
            )
        for r in evid:
            res = r.results.get("loss_ledger")
            if res is not None:
                lg.body += [finding_html(f, r.label) for f in res.findings]
                lg.body += [f"<p class='cap'>{e(r.label)}: {e(n)}</p>" for n in res.notes]
        out.append(lg)

    stg = [
        (r, r.results["stage_delta"]) for r in evid if "stage_delta" in r.results and r.results["stage_delta"].findings
    ]
    if stg:
        s = Chapter("stages", "What each stage changed (pretrain → finetune)", "A · Forensics")
        for r, res in stg:
            s.body += [finding_html(f, r.label) for f in res.findings]
        out.append(s)

    fm = Chapter("failures", "Where the model fails", "A · Forensics")
    for cls in _top_classes(evs):
        lines_ = []
        for lab, ev in evs.items():
            ce = ev.classes.get(cls)
            if ce and ce.auc_by_distance:
                rec = min(ce.auc_by_distance)
                pts = sorted(ce.auc_by_distance[rec].items())
                lines_.append((lab, [p[0] for p in pts], [p[1] for p in pts]))
        if lines_:
            fm.body.append(
                svgplot.line_chart(lines_, f"{cls}: AUC at the working point vs distance", "distance (m)", "AUC")
            )
    for cls in _top_classes(evs)[:2]:
        tails = [
            (lab, list(range(1, len(ev.classes[cls].worst_fp) + 1)), [v for _, v in ev.classes[cls].worst_fp])
            for lab, ev in evs.items()
            if cls in ev.classes and ev.classes[cls].worst_fp
        ]
        if tails:
            fm.body.append(
                svgplot.line_chart(tails, f"{cls}: worst sequences by FP per frame", "sequence rank", "avg FP / frame")
            )
    for r in evid:
        for name in ("eval_localization", "sequence_drilldown", "regression"):
            res = r.results.get(name)
            if res is not None:
                fm.body += [finding_html(f, r.label) for f in res.findings if not f.title.startswith("Shared ruler")]
    if len(fm.body):
        out.append(fm)

    hy = Chapter("hypotheses", "Hypotheses → evidence → verdict", "A · Forensics")
    if study.hypotheses:
        for h in study.hypotheses:
            hy.body.append(
                f'<div class="finding"><div class="ft"><b>{e(h.statement)}</b> '
                f'<span class="pill {e(h.status.value)}">{e(h.status.value)}</span></div>'
                + (f"<p>{e(h.rationale)}</p>" if h.rationale else "")
                + (f"<p><b>Proposed change:</b> {e(h.proposed_change)}</p>" if h.proposed_change else "")
                + (f"<p><b>Verdict:</b> {e(h.verdict)}</p>" if h.verdict else "")
                + (f"<p class='cap'>{len(h.evidence)} evidence link(s)</p>" if h.evidence else "")
                + "</div>"
            )
    else:
        hy.body.append("<p>No hypotheses recorded in this study yet. Candidate hypotheses from the findings:</p>")
        hs = {h.statement for r in evid for f in r.findings() for h in f.hypotheses}
        hy.body.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in sorted(hs)[:20]) + "</ul>")
    if study.notes:
        hy.body.append("<h3>Notes</h3><ul>" + "".join(f"<li>{e(n.text)}</li>" for n in study.notes) + "</ul>")
    out.append(hy)

    nx = Chapter("next", "Next steps", "C · Takeaways")
    steps = []
    remedies = {
        "P1": "Score every compared run with one ruler, or compare on torch_metric_evaluation only.",
        "P3": "Bin the GT of every scored attribute before trusting its error metric; drop degenerate ones.",
        "P4": "Point best_checkpoint_monitor at a metric that is logged (or add the validation loop).",
        "P5": "Add a validation loop to the training stage; select checkpoints on it.",
        "P8": "Stop at the plateau or change data / weights; do not extend the schedule.",
    }
    for r in evid:
        for chk in r.checks:
            if chk.status in {"FAIL", "WARN"} and chk.id in remedies and remedies[chk.id] not in steps:
                steps.append(remedies[chk.id])
    for r in evid:
        for f in sorted(r.findings(), key=lambda f: -f.severity.rank):
            for proposal in f.recommendations[:1]:
                step = f"{proposal.title} — {proposal.expected_outcome} ({r.label})"
                if step not in steps:
                    steps.append(step)
    nx.body.append(
        "<ol>" + "".join(f"<li>{e(s)}</li>" for s in steps[:15]) + "</ol>" if steps else "<p>No actions.</p>"
    )
    out.append(nx)

    src = Chapter("sources", "Evidence sources", "C · Takeaways")
    rows = []
    for r in evid:
        rows.append([r.label, "metric histories", f"{len(r.series)} series (stages merged, raw names kept)"])
        for ev in r.evals:
            rows.append(
                [
                    r.label,
                    f"evaluation JSONs{' · ' + ev.stage if ev.stage else ''}",
                    f"{ev.files} files under {ev.root}",
                ]
            )
        if r.lr is not None:
            rows.append([r.label, "learning rate", f"{r.lr.source} {r.lr.provenance.get('artifact', '')}"])
        rows.append([r.label, "configs", f"{len(r.config)} flat keys"])
    src.body.append(table(["Run", "Evidence", "Source"], rows))
    src.body.append(
        "<p class='cap'>Every table and figure in this book is computed from these sources by "
        "Model Forensic's deterministic analyses; regenerate with <code>mf study book</code>.</p>"
    )
    out.append(src)
    return sorted(out, key=lambda c: PART_ORDER.index(c.part) if c.part in PART_ORDER else 9)


def _find(r: RunEvidence, name: str, split: str) -> Any:
    for k, s in r.series.items():
        if k.name == name and k.split.value == split and not k.dims:
            return s
    return None


def _top_classes(evs: dict[str, EvalEvidence]) -> list[str]:
    counts: dict[str, int] = {}
    for ev in evs.values():
        for c, ce in ev.classes.items():
            if ce.auc_by_distance or ce.worst_fp:
                counts[c] = counts.get(c, 0) + 1
    return sorted(counts, key=lambda c: (-counts[c], c))[:4]


# --------------------------------------------------------------------------------------------- render
CSS = """
:root{--bg:#0e1116;--panel:#151a22;--text:#e6e9ef;--muted:#9aa4b2;--line:#263040;--accent:#7c8cff;--good:#2fb37e;
--bad:#e0565b;--warn:#d9a33f;--code:#1b2230}
@media (prefers-color-scheme: light){:root:not([data-theme=dark]){--bg:#f7f8fa;--panel:#fff;--text:#1b2230;--muted:#5b6575;
--line:#e3e7ee;--accent:#4f5bd5;--code:#f0f2f6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
.side{position:fixed;inset:0 auto 0 0;width:270px;overflow:auto;border-right:1px solid var(--line);background:var(--panel);padding:18px}
.side h1{font-size:15px;margin:0 0 4px}.side .sub{color:var(--muted);font-size:12px;margin-bottom:14px}
.side .part{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin:14px 0 4px}
.side a{display:block;color:var(--text);text-decoration:none;padding:4px 8px;border-radius:6px;font-size:13.5px}
.side a.on,.side a:hover{background:var(--code)}main{margin-left:270px;padding:28px 40px;max-width:1100px}
section{display:none}section.on{display:block}h2{margin-top:0}h3{margin-top:24px}
.tw{overflow-x:auto;margin:10px 0}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.verdict,.hypo,.keybox,.ai{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:10px 14px;margin:12px 0;background:var(--panel)}
.verdict{border-left-color:var(--good);font-weight:500}.keybox{border-left-color:var(--warn)}.ai{border-left-color:#9b6bdf}
.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:0 8px;font-size:11.5px;color:var(--muted)}
.pill.fail,.pill.rejected{color:var(--bad);border-color:var(--bad)}.pill.pass,.pill.supported{color:var(--good);border-color:var(--good)}
.pill.warn,.pill.inconclusive{color:var(--warn);border-color:var(--warn)}.pill.ghost{border-style:dashed}
.finding{border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0;background:var(--panel)}
.finding.sev-high,.finding.sev-critical{border-left:4px solid var(--bad)}.finding.sev-medium{border-left:4px solid var(--warn)}
.finding p{margin:6px 0}.finding ul{margin:4px 0 4px 18px;padding:0}.lbl{color:var(--muted);font-size:11px;text-transform:uppercase;margin-top:6px}
.who{color:var(--muted);font-size:12px}.cap{color:var(--muted);font-size:12.5px}code{background:var(--code);padding:1px 4px;border-radius:4px}
svg.chart{width:100%;max-width:720px;height:auto;margin:10px 0;display:block}svg .grid{stroke:var(--line)}svg .zero{stroke:var(--muted)}
svg text{fill:var(--muted);font-size:11px}svg .ct{fill:var(--text);font-size:13px;font-weight:600}svg .leg{font-size:10.5px}
.pager{display:flex;justify-content:space-between;margin-top:30px}.pager a{color:var(--accent)}
@media (max-width:820px){.side{position:static;width:auto;border-right:0}main{margin:0;padding:16px}}
"""
JS = """
function show(id){document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===id));
document.querySelectorAll('.side a').forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+id));window.scrollTo(0,0)}
window.addEventListener('hashchange',()=>show(location.hash.slice(1)||document.querySelector('section').id));
show(location.hash.slice(1)||document.querySelector('section').id);
"""


def render(study: Study, chs: list[Chapter], generated: str) -> str:
    nav, last = [], None
    for i, c in enumerate(chs, 1):
        if c.part != last:
            nav.append(f'<div class="part">{e(c.part)}</div>')
            last = c.part
        nav.append(f'<a href="#{c.id}">{i}. {e(c.title)}</a>')
    secs = []
    for i, c in enumerate(chs):
        prev_ = f'<a href="#{chs[i - 1].id}">← {e(chs[i - 1].title)}</a>' if i else "<span></span>"
        next_ = f'<a href="#{chs[i + 1].id}">{e(chs[i + 1].title)} →</a>' if i + 1 < len(chs) else "<span></span>"
        secs.append(
            f'<section id="{c.id}"><h2>{i + 1}. {e(c.title)}</h2>'
            + "".join(c.body)
            + f'<div class="pager">{prev_}{next_}</div></section>'
        )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        f'initial-scale=1"><title>{e(study.title)} — study book</title><style>{CSS}</style></head><body>'
        f'<nav class="side"><h1>{e(study.title)}</h1><div class="sub">Study book · generated {e(generated)}</div>'
        + "".join(nav)
        + f"</nav><main>{''.join(secs)}</main><script>{JS}</script></body></html>"
    )


def summary(study: Study, evid: list[RunEvidence], chs: list[Chapter], generated: str) -> dict[str, Any]:
    head, lines = verdict(evid)
    return {
        "study": {"id": study.id, "title": study.title, "goal": study.goal},
        "generated_at": generated,
        "verdict": head,
        "verdict_points": lines,
        "runs": [
            {
                "label": r.label,
                "run": str(r.run.ref),
                "role": r.role,
                "stages": r.stages,
                "ruler": r.ruler,
                "headline": r.headline,
                "checks": {c.id: {"status": c.status, "detail": c.detail} for c in r.checks},
                "findings": [{"title": f.title, "severity": f.severity.value} for f in r.findings()],
            }
            for r in evid
        ],
        "chapters": [c.title for c in chs],
    }


def build(
    svc: ForensicService,
    study: Study,
    ai: Callable[[str, list[RunEvidence]], str | None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    if not study.runs and study.baseline is None:
        raise ValueError("the study has no runs")
    evid = gather(svc, study, progress)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ai_text = None
    if ai is not None:
        head, lines = verdict(evid)
        try:
            ai_text = ai(head + "\n" + "\n".join(lines), evid)
        except Exception as exc:  # the book never depends on the LLM
            ai_text = f"(AI narrative unavailable: {type(exc).__name__})"
    chs = chapters(study, evid, ai_text)
    return render(study, chs, generated), summary(study, evid, chs, generated)


def dump_summary(s: dict[str, Any]) -> str:
    return json.dumps(s, indent=2, default=str)


_ = RunRef  # re-exported type for callers
