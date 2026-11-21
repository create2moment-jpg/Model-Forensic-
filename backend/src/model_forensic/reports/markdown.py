"""Forensic report generation (Markdown; HTML for print-to-PDF).

Reports are generated from stored, structured objects only (comparison, findings, investigation),
so every statement is traceable to its provenance.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from model_forensic.analysis.base import AnalysisResult
from model_forensic.domain.comparison import Comparison, DeltaVerdict
from model_forensic.domain.findings import Finding
from model_forensic.domain.run import Run

_ICON = {"critical": "🟥", "high": "🟧", "medium": "🟨", "low": "🟦", "info": "⬜"}


def _f(v: float | None, d: int = 4) -> str:
    return "—" if v is None else f"{v:.{d}g}"


def build_report(
    title: str,
    runs: Sequence[tuple[str, Run]],
    results: Sequence[AnalysisResult],
    comparison: Comparison | None = None,
    investigation: dict[str, Any] | None = None,
    problem: str | None = None,
) -> str:
    findings: list[Finding] = sorted((f for r in results for f in r.findings), key=lambda f: -f.severity.rank)
    out: list[str] = [
        f"# {title}",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by Model Forensic._",
        "",
    ]

    out += ["## Executive Summary", ""]
    if findings:
        top = findings[:3]
        out.append(f"{len(findings)} finding(s); highest severity **{findings[0].severity.value.upper()}**.")
        out += [f"- {_ICON[f.severity.value]} **{f.title}** — {f.statement}" for f in top]
    else:
        out.append("No findings above thresholds.")
    if investigation and investigation.get("summary"):
        out += [
            "",
            f"> AI interpretation ({investigation.get('model')}, {investigation.get('verified_ratio', 0):.0%} of claims verified): "
            f"{investigation['summary']}",
        ]
    out.append("")

    out += [
        "## Problem Statement",
        "",
        problem or "Explain the behaviour of the runs below and identify likely causes.",
        "",
    ]

    out += [
        "## Runs Investigated",
        "",
        "| Role | Run | Status | Experiment | Commit | Branch | Duration (h) | GPU-h |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for role, r in runs:
        out.append(
            f"| {role} | `{r.ref}` | {r.status.value} | {r.experiment or '—'} | `{(r.git.commit or '—')[:10]}` | "
            f"{r.git.branch or '—'} | {_f((r.duration_s or 0) / 3600, 3)} | {_f(r.gpu_hours, 3)} |"
        )
    out.append("")

    if comparison:
        cd = comparison.config_diff
        out += ["## Configuration Differences", ""]
        if cd.meaningful:
            out += ["| Path | Baseline | Candidate | Category |", "|---|---|---|---|"]
            out += [f"| `{c.path}` | `{c.baseline!r}` | `{c.candidate!r}` | {c.category} |" for c in cd.meaningful]
        else:
            out.append("No meaningful configuration differences.")
        noise = [c for c in cd.changes if c.is_noise]
        if noise:
            out.append(f"\n_{len(noise)} runtime-only difference(s) ignored (paths, names)._")
        out += ["", "## Metric Comparison", ""]
        basis = (
            "replicate seed variance" if comparison.baseline_replicates else "fixed relative thresholds (no replicates)"
        )
        out.append(f"Significance basis: **{basis}**.\n")
        out += ["| Metric | Baseline | Candidate | Δ | Δ% | z | Verdict |", "|---|---|---|---|---|---|---|"]
        for d in sorted(comparison.deltas, key=lambda d: (d.verdict != DeltaVerdict.REGRESSED, d.metric)):
            out.append(
                f"| {d.metric} | {_f(d.baseline)} | {_f(d.candidate)} | {d.absolute:+.4g} | "
                f"{'—' if d.relative is None else f'{d.relative:+.1%}'} | {_f(d.z_score, 2)} | {d.verdict.value} |"
            )
        out.append("")

    sections = [
        ("Training Dynamics", "training_dynamics"),
        ("Loss Analysis", "loss"),
        ("Evaluation Analysis", "regression"),
        ("Configuration Attribution", "config"),
        ("Sparse Detection", "sparse_detection"),
    ]
    for name, cat in sections:
        fs = [f for f in findings if f.category == cat]
        skipped = [res for res in results if res.skipped_reason and _cat(res.analysis) == cat]
        if not fs and not skipped:
            continue
        out += [f"## {name}", ""]
        for res in skipped:
            out.append(f"_Not analyzed: {res.skipped_reason}._")
        for f in fs:
            out += _finding_md(f)
    out += [
        "## Multimodal Analysis",
        "",
        "_Modality-ablation analysis requires an ablation run set (planned, Phase 4); "
        "range-sliced evidence appears under Evaluation Analysis._",
        "",
        "## Temporal Analysis",
        "",
        "_Temporal-window analysis requires sequence-level metrics (planned, Phase 4)._",
        "",
    ]

    hyps = [(f, h) for f in findings for h in f.hypotheses]
    out += ["## Hypotheses", ""]
    out += [
        f"- **[{h.confidence.value}]** {h.statement} _(from {f.title}; falsify by: {h.falsifiable_by or '—'})_"
        for f, h in hyps
    ] or ["None."]
    out += ["", "## Alternative Explanations", ""]
    out += [
        "- Seed variance (unless replicate-based significance is reported above).",
        "- Code changes between commits not captured in configuration.",
        "- Data or evaluation-protocol version drift.",
    ]
    if investigation:
        alts = [c for c in investigation.get("claims", []) if c["type"] == "HYPOTHESIS"]
        out += [f"- (AI{'' if c['verified'] else ', UNVERIFIED'}) {c['text']}" for c in alts]
    out += ["", "## Recommended Experiments", ""]
    recs = sorted(
        {r.id: r for f in findings for r in f.recommendations}.values(), key=lambda r: (r.priority, -r.information_gain)
    )
    if recs:
        out += [
            "| # | Experiment | Controlled | Measure | Accept if | Cost (GPU-h) | Info gain |",
            "|---|---|---|---|---|---|---|",
        ]
        for rec in recs:
            out.append(
                f"| {rec.priority} | {rec.title} | `{rec.controlled_variable}` | {', '.join(rec.measurements[:3])} | "
                f"{rec.acceptance_criterion} | {_f(rec.estimated_cost_gpu_hours, 3)} | {rec.information_gain:.2f} |"
            )
    else:
        out.append("None.")
    out += ["", "## Confidence", ""]
    out += [f"- {f.title}: **{f.confidence.value}** ({f.label.value})" for f in findings] or ["—"]
    out += ["", "## Limitations", ""]
    lims = sorted({lim for f in findings for lim in f.limitations})
    out += [f"- {lim}" for lim in lims] or ["—"]
    if investigation:
        unver = [c for c in investigation.get("claims", []) if not c["verified"]]
        if unver:
            out.append(f"- {len(unver)} AI claim(s) failed grounding checks and are marked UNVERIFIED.")
    out += ["", "## Appendix", "", "### Provenance", ""]
    out += ["| Finding | Analysis | Version | Params hash | Runs |", "|---|---|---|---|---|"]
    for f in findings:
        p = f.provenance
        out.append(
            f"| `{f.id}` | {p.analysis} | {p.analysis_version} | `{p.params_hash}` | {', '.join(f'`{r}`' for r in p.runs)} |"
        )
    if investigation:
        out += [
            "",
            "### AI investigation",
            "",
            f"- id `{investigation['id']}`, model `{investigation['model']}`, prompt "
            f"`{investigation.get('prompt') or 'ad-hoc'}`, context hash `{investigation['context_hash']}`",
            "",
        ]
        for c in investigation.get("claims", []):
            flag = "✓" if c["verified"] else "✗ UNVERIFIED (" + "; ".join(c["problems"]) + ")"
            out.append(f"- **{c['type']}** {c['text']} [{', '.join(c['evidence'])}] {flag}")
    return "\n".join(out) + "\n"


def _cat(analysis: str) -> str:
    return {
        "training_dynamics": "training_dynamics",
        "loss_forensics": "loss",
        "regression": "regression",
        "config_attribution": "config",
        "sparse_detection": "sparse_detection",
    }.get(analysis, analysis)


def _finding_md(f: Finding) -> list[str]:
    lines = [
        f"### {_ICON[f.severity.value]} {f.title}",
        "",
        f"**Severity** {f.severity.value} · **Confidence** {f.confidence.value} · **Label** {f.label.value} · `{f.id}`",
        "",
        f.statement,
        "",
    ]
    if f.evidence:
        lines.append("Evidence:")
        lines += [f"- _{e.label.value}_: {e.statement}" for e in f.evidence]
        lines.append("")
    return lines


def to_html(markdown_text: str, title: str = "Model Forensic report") -> str:
    """Minimal, dependency-free Markdown→HTML for print-to-PDF. Tables, headings, lists, emphasis."""
    import re

    body: list[str] = []
    in_table = in_list = False
    for line in markdown_text.splitlines():
        esc = html.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
        esc = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", esc)
        if line.startswith("|"):
            cells = [c.strip() for c in esc.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                body.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{esc[2:]}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        m = re.match(r"^(#{1,4}) (.*)", esc)
        if m:
            n = len(m.group(1))
            body.append(f"<h{n}>{m.group(2)}</h{n}>")
        elif line.startswith("&gt; ") or line.startswith("> "):
            body.append(f"<blockquote>{esc[5:] if esc.startswith('&gt; ') else esc[2:]}</blockquote>")
        elif line.strip():
            body.append(f"<p>{esc}</p>")
    if in_table:
        body.append("</table>")
    if in_list:
        body.append("</ul>")
    css = (
        "body{font:14px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;color:#111}"
        "table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #ccc;padding:3px 8px;font-size:12px}"
        "th{background:#f3f3f3}code{background:#f3f3f3;padding:0 3px}blockquote{border-left:3px solid #888;margin:0;padding-left:1rem;color:#444}"
        "@media print{h2{page-break-before:auto}}"
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>{''.join(body)}</body></html>"
