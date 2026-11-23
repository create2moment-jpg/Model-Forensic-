"""Study book parts: Part 1 studies the baseline on its own, Part 2 has one chapter per run compared with the
baseline, Part 3 concludes. Deterministic; the team knowledge is applied (what it sets aside is marked, never
dropped silently). Rendering only — the numbers come from ``analysis.baseline_study`` and the other analyses."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from model_forensic.analysis import baseline_study as bs
from model_forensic.analysis import shared_ruler as sr
from model_forensic.analysis.config_diff import ConfigDiffer
from model_forensic.analysis.divergence import diverge
from model_forensic.domain.config import ChangeKind, ConfigChange
from model_forensic.domain.metrics import Split
from model_forensic.reports import svgplot

if TYPE_CHECKING:
    from model_forensic.knowledge import Applied
    from model_forensic.reports.study_book import Chapter, RunEvidence

P1 = "Part 1 · The baseline"
P2 = "Part 2 · Each run against the baseline"
P3 = "Part 3 · Conclusions"
FAMILY = {
    "decoder_cls": "Decoder · class",
    "decoder_bbox": "Decoder · box",
    "denoise_cls": "Denoising · class",
    "denoise_bbox": "Denoising · box",
    "camera": "Camera head",
    "radar": "Radar head",
    "other": "Other",
}
VERDICT_CLASS = {
    "consistent": "pass",
    "mismatch": "warn",
    "unverifiable": "skip",
    "note": "skip",
    "self-muted": "fail",
    "down-weighted": "warn",
    "up-weighted": "pass",
    "neutral": "skip",
    "still learning": "pass",
    "plateaued": "skip",
    "rising": "fail",
    "flat": "warn",
}


@dataclass
class BaselineStudy:
    progress: list[bs.FamilyProgress]
    totals: dict[str, bs.FamilyProgress]
    weights: list[bs.TermWeight]
    readings: list[bs.ConfigReading]
    config: dict[str, Any]
    disagree: list[tuple[str, list[str]]]
    conclusions: list[str] = field(default_factory=list)


def study_run(r: RunEvidence) -> BaselineStudy:
    weights = bs.uncertainty_weights(r.series)
    cfg = bs.effective_config(r.config, r.stages)
    return BaselineStudy(
        bs.loss_progress(r.series),
        bs.total_progress(r.series),
        weights,
        bs.config_vs_behaviour(cfg, r.series, weights, r.lr),
        cfg,
        bs.sources_disagree(r.config, r.stages),
    )


def _e(x: Any) -> str:
    import html

    return html.escape(str(x))


def _pill(v: str) -> tuple[str, str]:
    return ("html", f'<span class="pill {VERDICT_CLASS.get(v, "skip")}">{_e(v)}</span>')


def _aside(kn: Applied | None, keys: list[str] | str) -> str | None:
    if kn is None:
        return None
    for k in [keys] if isinstance(keys, str) else keys:
        eid = kn.metric_reason(k)
        if eid:
            return eid
    return None


def _aside_cell(eid: str | None) -> tuple[str, str] | str:
    return (
        ("html", f'<span class="pill ghost" title="set aside by team knowledge">set aside · {_e(eid)}</span>')
        if eid
        else ""
    )


def _key_aside(kn: Applied | None, key: str) -> str | None:
    """Entry id when a metric key, or the class it is about, is set aside."""
    if kn is None:
        return None
    from model_forensic.domain.metrics import MetricKey

    try:
        cls = MetricKey.parse(key).dim_dict.get("class")
    except Exception:  # not a canonical key
        cls = None
    return kn.metric_reason(key) or (kn.class_reason(cls) if cls else None)


def _frac(n: int) -> list[float]:
    return [(i + 0.5) / n for i in range(n)]


def _pct(v: float | None) -> str:
    return "—" if v is None or not math.isfinite(v) else f"{v:.0%}"


# ------------------------------------------------------------------------------------------------ Part 1
def baseline_chapters(r: RunEvidence, b: BaselineStudy, *, chosen: bool) -> list[Chapter]:
    from model_forensic.reports.study_book import Chapter, box, table

    kn = r.knowledge
    out: list[Chapter] = []

    # 1.1 the baseline at a glance + the knowledge applied to it
    g = Chapter("baseline", f"The baseline — {r.label}", P1)
    if not chosen:
        g.body.append(box("keybox", "No baseline is set for this study; the first run is studied as the reference."))
    g.body.append(
        table(
            ["Run", "Stages", "Status", "Ruler", "Headline", "Datasets"],
            [
                [
                    r.label,
                    " → ".join(r.stages) or "single job",
                    r.run.status.value,
                    r.ruler or "—",
                    r.headline if r.headline is not None else "—",
                    ", ".join(d.name for d in r.run.datasets) or "—",
                ]
            ],
        )
    )
    if kn is not None and kn.entries:
        g.body.append("<h3>What the team knows about this run</h3>")
        g.body.append(
            table(
                ["Entry", "Kind", "Statement", "Changes here"],
                [
                    [
                        en.id,
                        en.kind,
                        en.statement,
                        "; ".join(
                            [f"set aside metric {m}" for m in en.effects.ignore_metrics]
                            + [f"set aside class {c}" for c in en.effects.ignore_classes]
                            + ([f"setup {en.effects.setup}"] if en.effects.setup else [])
                            + ([en.effects.caveat] if en.effects.caveat else [])
                        )
                        or "—",
                    ]
                    for en in kn.entries
                ],
            )
        )
        if kn.setups:
            g.body.append(f"<p>Setup: <b>{_e(', '.join(kn.setups))}</b></p>")
        for text, eid in kn.caveats:
            g.body.append(box("keybox", f"<b>Caveat</b> ({_e(eid)}): {_e(text)}"))
    else:
        g.body.append(
            "<p class='cap'>No team knowledge applies to this run (page <i>Knowledge</i>, "
            "<code>config/knowledge/*.local.yaml</code>).</p>"
        )
    out.append(g)

    # 1.2 loss progress over time
    if b.progress:
        lp = Chapter("baseline-losses", "How each loss progressed over training", P1)
        lp.body.append(
            box(
                "hypo",
                "Each family is the sum of its terms' <b>raw</b> loss (<code>uloss</code>: what the network "
                "achieves, independent of the uncertainty weighting), read per 1 % of training. <i>Still learning</i> "
                "= still falling over the last 20 %; <i>plateaued</i> = the drop is over; <i>rising</i> = the network "
                "is getting worse at it (often a term σ muted, or targets it cannot fit). How much of each loss "
                "reaches the gradient is the next chapter.",
            )
        )
        n = len(b.progress[0].curve)
        lp.body.append(
            svgplot.line_chart(
                [
                    (FAMILY.get(p.family, p.family), _frac(n), [v / (p.start or 1.0) for v in p.curve])
                    for p in b.progress[:8]
                    if p.start
                ],
                "Loss per family, relative to its start",
                "fraction of training",
                "loss / start",
            )
        )
        if b.totals:
            lp.body.append(
                svgplot.line_chart(
                    [(name, _frac(len(p.curve)), p.curve) for name, p in b.totals.items()],
                    "Totals: raw (uloss), network-facing (loss_noreg), weighted (loss)",
                    "fraction of training",
                    "loss",
                )
            )
        lp.body.append(
            table(
                [
                    "Family",
                    "Terms",
                    "Start",
                    "End",
                    "Change",
                    "Half the drop by",
                    "90 % by",
                    "Last 20 %",
                    "Reading",
                    "",
                ],
                [
                    [
                        FAMILY.get(p.family, p.family),
                        p.terms,
                        p.start,
                        p.end,
                        f"{p.change:+.0%}",
                        _pct(p.t50),
                        _pct(p.t90),
                        f"{p.late_change:+.1%}",
                        _pill(p.verdict),
                        _aside_cell(_aside(kn, p.keys)),
                    ]
                    for p in b.progress
                ],
            )
        )
        learning = [FAMILY.get(p.family, p.family) for p in b.progress if p.verdict == "still learning"]
        rising = [FAMILY.get(p.family, p.family) for p in b.progress if p.verdict == "rising"]
        early = [FAMILY.get(p.family, p.family) for p in b.progress if p.t90 is not None and p.t90 <= 0.25]
        lines = []
        if early:
            lines.append(f"{', '.join(early)} finished learning within the first quarter of training.")
        if learning:
            lines.append(f"{', '.join(learning)} still falls at the end — more steps would still buy loss here.")
        if rising:
            lines.append(
                f"{', '.join(rising)} rises over training — see the uncertainty chapter: a term the weighting "
                "favours elsewhere, or one whose targets the network cannot fit."
            )
        if lines:
            lp.body.append(box("verdict", "<br>".join(_e(x) for x in lines)))
            b.conclusions += lines
        out.append(lp)

    # 1.3 uncertainty weighting
    if b.weights:
        uw = Chapter("baseline-sigma", "Uncertainty weighting — which losses σ trusts and which it mutes", P1)
        uw.body.append(
            box(
                "hypo",
                "With learned uncertainty weighting each term's effective weight is <code>exp(−2·log σ)</code>. "
                "A term whose σ grows is being <b>muted</b> (the model declares it unpredictable); one whose σ "
                "shrinks is <b>trusted</b>. Weights are compared with the median term at the end of training.",
            )
        )
        curves = bs.family_weight_curves(b.weights)
        uw.body.append(
            svgplot.line_chart(
                [(FAMILY.get(f, f), _frac(len(c)), c) for f, c in curves.items()],
                "Mean effective weight exp(−2 log σ) per family",
                "fraction of training",
                "weight (log scale)",
                log_y=True,
            )
        )
        uw.body.append(
            table(
                [
                    "Term",
                    "Family",
                    "log σ start → end",
                    "Weight start → end",
                    "vs median",
                    "Trend",
                    "Reading",
                    "Gradient share",
                    "",
                ],
                [
                    [
                        w.term,
                        FAMILY.get(w.family, w.family),
                        f"{w.s_start:+.2f} → {w.s_end:+.2f}",
                        f"{w.w_start:.3g} → {w.w_end:.3g}",
                        f"{w.rel_to_median:.2g}×",
                        w.trend,
                        _pill(w.verdict),
                        _pct(w.grad_share),
                        _aside_cell(_aside(kn, [w.key, w.term.split("[")[0]])),
                    ]
                    for w in b.weights
                ],
            )
        )
        muted = [w for w in b.weights if w.verdict == "self-muted"]
        up = [w for w in b.weights if w.verdict == "up-weighted"]
        lines = []
        if muted:
            fams = sorted({FAMILY.get(w.family, w.family) for w in muted})
            lines.append(
                f"σ mutes {len(muted)} term(s) ({', '.join(fams)}): effective weight below 10 % of the median — "
                "they barely train the network any more. Muted is not useless: check whether their raw loss still "
                "falls and whether the modality matters at evaluation."
            )
        if up:
            fams = sorted({FAMILY.get(w.family, w.family) for w in up})
            share = sum(w.grad_share or 0.0 for w in up)
            lines.append(
                f"σ trusts {', '.join(fams)} most (weight > 2× the median)"
                + (
                    f", yet these terms carry only {share:.0%} of the network-facing loss — their raw loss is small, "
                    "so trust is not share."
                    if share < 0.25
                    else f" and they carry {share:.0%} of the network-facing loss — they dominate the gradient."
                )
            )
        losing = [w for w in b.weights if w.trend == "losing trust" and w.verdict != "self-muted"]
        if losing:
            lines.append(f"{len(losing)} more term(s) are losing trust over training (σ still rising at the end).")
        if lines:
            uw.body.append(box("verdict", "<br>".join(_e(x) for x in lines)))
            b.conclusions += lines
        out.append(uw)

    # 1.4 configuration vs behaviour
    if b.readings or b.disagree:
        cb = Chapter("baseline-config", "The composed configuration against what the run did", P1)
        cb.body.append(
            box(
                "hypo",
                "The composed config is what the training code was given; the logs are what happened. Each row "
                "checks one promise of the config against the metrics. A mismatch is not a bug by itself — it says "
                "the config is not the whole story (another switch, a scaling rule, σ).",
            )
        )
        if b.readings:
            cb.body.append(
                table(
                    ["Topic", "Config", "Observed", "Reading", "Why it matters"],
                    [[x.topic, x.config, x.observed, _pill(x.verdict), x.detail] for x in b.readings],
                )
            )
        if b.disagree:
            cb.body.append("<h3>Submitted vs composed config disagree</h3>")
            cb.body.append(table(["Key", "Values"], [[k, " · ".join(v)] for k, v in b.disagree]))
        bad = [x for x in b.readings if x.verdict == "mismatch"]
        if bad:
            lines = [f"{x.topic}: {x.config} — but {x.observed}. {x.detail}".strip() for x in bad]
            cb.body.append(box("verdict", "<br>".join(_e(x) for x in lines)))
            b.conclusions += lines
        out.append(cb)
    return out


# ------------------------------------------------------------------------------------------------ Part 2
def _diff(base_cfg: dict[str, Any], cand_cfg: dict[str, Any]) -> list[ConfigChange]:
    d = ConfigDiffer()
    out = []
    for k in sorted(set(base_cfg) | set(cand_cfg)):
        a, c = base_cfg.get(k), cand_cfg.get(k)
        if k in base_cfg and k in cand_cfg and str(a) == str(c):
            continue
        if d.is_noise(k) or k.startswith("params."):
            continue
        kind = (
            ChangeKind.CHANGED
            if k in base_cfg and k in cand_cfg
            else (ChangeKind.ADDED if k in cand_cfg else ChangeKind.REMOVED)
        )
        out.append(ConfigChange(path=k, kind=kind, baseline=a, candidate=c, category=d.categorize(k)))
    return out


def _train_total(r: RunEvidence, name: str) -> Any:
    for k, s in r.series.items():
        if k.name == name and k.split == Split.TRAIN and not k.dims:
            return s
    return None


BUBBLE = {"dark_green": "#137a3f", "green": "#2fb37e", "yellow": "#d9a520", "red": "#e0565b", "grey": "#8a8f98"}


def _facts(r: RunEvidence, st: BaselineStudy) -> Any:
    from model_forensic.application.beliefs import final_value
    from model_forensic.knowledge.beliefs import RunFacts

    finals = {str(k): v for k, s in r.series.items() if (v := final_value(s.values)) is not None}
    return RunFacts(str(r.run.ref), r.label, st.config, finals)


def experience_rows(
    base: RunEvidence, bstudy: BaselineStudy, cand: RunEvidence, cstudy: BaselineStudy, beliefs: list[Any]
) -> list[list[Any]]:
    """Beliefs (knowledge from experience) whose config key differs between the two runs, checked on this pair."""
    from model_forensic.knowledge.beliefs import find_key, pair_outcome

    rows: list[list[Any]] = []
    fa, fb = _facts(base, bstudy), _facts(cand, cstudy)
    for entry, summ in beliefs:
        t = entry.test
        if t is None or t.type != "config_effect" or entry.status == "retired":
            continue
        ka, kb = find_key(fa.config, t.key), find_key(fb.config, t.key)
        if ka is None or kb is None or str(fa.config[ka]) == str(fb.config[kb]):
            continue
        ev = pair_outcome(t, fa, fb)
        colour = (summ or {}).get("colour", "grey")
        overall = (
            (
                "html",
                f'<span class="bubble" style="background:{BUBBLE.get(colour, "#8a8f98")}"></span> '
                f"{_e(colour.replace('_', ' '))} · {(summ or {}).get('p', 0.5):.0%}",
            )
            if summ
            else "not checked yet"
        )
        rows.append(
            [
                entry.statement,
                overall,
                ev.outcome.replace("_", " ") if ev else "metric not logged by both",
                ev.detail if ev else f"{t.key}: {fa.config[ka]} → {fb.config[kb]}",
            ]
        )
    return rows


def comparison_chapter(
    base: RunEvidence,
    bstudy: BaselineStudy,
    cand: RunEvidence,
    cstudy: BaselineStudy,
    idx: int,
    beliefs: list[Any] | None = None,
) -> tuple[Chapter, list[str]]:
    from model_forensic.reports.study_book import Chapter, box, finding_html, table

    ch = Chapter(f"vs-{idx}", f"{cand.label} against the baseline", P2)
    concl: list[str] = []
    kn = cand.knowledge

    # what changed
    changes = _diff(bstudy.config, cstudy.config)
    ch.body.append("<h3>What changed in the configuration</h3>")
    if changes:
        cats: dict[str, int] = {}
        for c in changes:
            cats[c.category] = cats.get(c.category, 0) + 1
        ch.body.append(
            "<p>"
            + _e(f"{len(changes)} setting(s) differ: " + ", ".join(f"{n} {c}" for c, n in sorted(cats.items())))
            + "</p>"
        )
        ch.body.append(
            table(
                ["Category", "Key", "Baseline", cand.label],
                [
                    [
                        c.category,
                        c.path,
                        "—" if c.baseline is None else c.baseline,
                        "—" if c.candidate is None else c.candidate,
                    ]
                    for c in sorted(changes, key=lambda c: (c.category, c.path))[:40]
                ],
            )
        )
        if len(changes) > 40:
            ch.body.append(f"<p class='cap'>… and {len(changes) - 40} more.</p>")
        main = [c for c in changes if c.category not in {"other", "randomness", "infrastructure"}][:3]
        if main:
            concl.append("Main config changes: " + "; ".join(f"{c.path} {c.baseline} → {c.candidate}" for c in main))
    else:
        ch.body.append("<p>No configuration differences outside noise keys (paths, seeds, ids).</p>")
    if cand.knowledge is not None and base.knowledge is not None:
        only = [en for en in cand.knowledge.entries if en.id not in {x.id for x in base.knowledge.entries}]
        if only:
            ch.body.append(
                box(
                    "keybox",
                    "<b>Knowledge that applies to this run but not to the baseline:</b> "
                    + "; ".join(f"{_e(en.id)} — {_e(en.statement)}" for en in only),
                )
            )

    # loss over time
    ch.body.append("<h3>Losses over training</h3>")
    overlay = []
    for r, st in ((base, bstudy), (cand, cstudy)):
        p = st.totals.get("uloss") or st.totals.get("loss_noreg")
        if p is not None:
            overlay.append((r.label, _frac(len(p.curve)), p.curve))
    if overlay:
        ch.body.append(svgplot.line_chart(overlay, "Total raw loss (uloss)", "fraction of training", "uloss"))
    bp = {p.family: p for p in bstudy.progress}
    rows = []
    for p in cstudy.progress:
        q = bp.get(p.family)
        if q is None:
            continue
        rel = (p.end - q.end) / abs(q.end) if q.end else math.nan
        rows.append(
            [
                FAMILY.get(p.family, p.family),
                q.end,
                p.end,
                f"{rel:+.0%}" if math.isfinite(rel) else "—",
                _pill(q.verdict),
                _pill(p.verdict),
                _aside_cell(_aside(kn, p.keys)),
            ]
        )
    if rows:
        ch.body.append(table(["Family", "Baseline end", f"{cand.label} end", "Δ", "Baseline", cand.label, ""], rows))
        moved = [r_ for r_ in rows if isinstance(r_[3], str) and r_[3] not in {"—"} and abs(float(r_[3][:-1])) >= 20]
        if moved:
            concl.append("Loss families that end ≥ 20 % apart: " + ", ".join(f"{r_[0]} {r_[3]}" for r_ in moved[:4]))

    # σ differences
    bw = {w.term: w for w in bstudy.weights}
    srows = []
    for w in cstudy.weights:
        v = bw.get(w.term)
        if v is None or not v.w_end:
            continue
        ratio = w.w_end / v.w_end
        if v.verdict != w.verdict or ratio > 2 or ratio < 0.5:
            srows.append(
                [w.term, f"{v.w_end:.3g}", f"{w.w_end:.3g}", f"{ratio:.2g}×", _pill(v.verdict), _pill(w.verdict)]
            )
    if srows:
        ch.body.append("<h3>Where the uncertainty weighting moved</h3>")
        ch.body.append(table(["Term", "Baseline weight", "Weight", "Ratio", "Baseline", cand.label], srows[:25]))
        concl.append(f"σ re-weighted {len(srows)} term(s) differently from the baseline")

    # where and why the curves diverge
    reg = cand.registry
    pairs = []
    for name in ("loss_noreg", "uloss", "loss"):
        a, c = _train_total(base, name), _train_total(cand, name)
        if a is not None and c is not None:
            pairs.append((f"train/{name}", a, c))
    shared = {str(k): s for k, s in base.series.items() if k.split != Split.TRAIN}
    for k, s in cand.series.items():
        if str(k) in shared and k.name in {"score", "mean_f1_score", "combined_metric"} and len(pairs) < 8:
            pairs.append((str(k), shared[str(k)], s))
    drows = []
    for key, a, c in pairs:
        dv = diverge(cand.label, a, c, registry=reg, ref_lr=base.lr, cand_lr=cand.lr, changes=changes)
        drows.append(
            [
                key + (f" (set aside · {aside_k})" if (aside_k := _key_aside(kn, key)) else ""),
                dv.outcome,
                "—" if dv.diverge_fraction is None else f"{dv.diverge_fraction:.0%} of training",
                "—" if dv.final_gap is None else f"{dv.final_gap:+.1%}",
                dv.sentence,
                "; ".join(dv.fits[:2]) or "—",
            ]
        )
        if key == "train/loss_noreg" and dv.outcome not in {"same", "insufficient"}:
            concl.append(f"Network-facing loss: {dv.sentence}")
    if drows:
        ch.body.append("<h3>Where and why the curves diverge</h3>")
        ch.body.append(
            table(
                ["Metric", "Outcome", "Diverges at", "Final gap (+ = better)", "Reading", "Config changes that fit"],
                drows,
            )
        )

    # per-class on the shared ruler
    eb, ec = sr.last_eval(base.evals), sr.last_eval(cand.evals)
    if eb is not None and ec is not None:
        t = sr.shared_table({base.label: eb, cand.label: ec})
        crow = []
        better = worse = 0
        for cell in t.cells:
            if base.label not in cell.values or cand.label not in cell.values:
                continue
            va, vc = cell.values[base.label], cell.values[cand.label]
            aside = (kn.class_reason(cell.cls) or kn.metric_reason(cell.metric)) if kn is not None else None
            sign = ""
            if cell.metric in sr.HIGHER or cell.metric in sr.LOWER:
                good = vc > va if cell.metric in sr.HIGHER else vc < va
                if vc != va and not aside:
                    better += int(good)
                    worse += int(not good)
                sign = "better" if good and vc != va else "worse" if vc != va else "same"
            crow.append([cell.cls, cell.metric, va, vc, f"{vc - va:+.4g}", sign, _aside_cell(aside)])
        if crow:
            ch.body.append("<h3>Per class on the shared ruler</h3>")
            ch.body.append(table(["Class", "Metric", "Baseline", cand.label, "Δ", "", ""], crow))
            concl.append(
                f"Shared ruler: better in {better}, worse in {worse} class×metric cells"
                + (
                    " (set-aside classes and metrics not counted)"
                    if kn is not None and (kn.ignore_classes or kn.ignore_metrics)
                    else ""
                )
            )

    exp = experience_rows(base, bstudy, cand, cstudy, beliefs or [])
    if exp:
        ch.body.append("<h3>Your experience, checked on this comparison</h3>")
        ch.body.append(
            "<p class='cap'>Beliefs from the Knowledge page (<i>Experience</i>) whose setting changed between these two "
            "runs. The colour is the belief's overall standing across all runs in its scope.</p>"
        )
        ch.body.append(table(["Belief", "Overall", "This comparison", "Detail"], exp))
        for row in exp:
            if row[2] in ("supports", "contradicts"):
                concl.append(f"Your belief “{row[0]}” — this comparison {row[2]} it")

    for name in ("regression", "stage_delta"):
        res = cand.results.get(name)
        if res is not None and res.findings:
            ch.body.append(f"<h3>{'Regression vs the baseline' if name == 'regression' else 'Stage changes'}</h3>")
            from model_forensic.application.knowledge import set_aside

            for f in res.findings[:6]:
                why = set_aside(f, kn) if kn is not None else None
                if why:
                    ch.body.append(
                        f"<p class='cap'>Set aside by team knowledge ({_e(why['entry'])}): {_e(why['reason'])}</p>"
                    )
                ch.body.append(finding_html(f, cand.label))

    if concl:
        ch.body.append("<h3>In short</h3>" + box("verdict", "<br>".join(_e(x) for x in concl)))
    else:
        ch.body.append(box("verdict", "No difference from the baseline beyond the noise was found."))
    return ch, concl


# ------------------------------------------------------------------------------------------------ Part 3
def conclusions_chapter(
    head: str,
    evid: list[RunEvidence],
    base: RunEvidence,
    bstudy: BaselineStudy,
    per_run: list[tuple[str, list[str]]],
    steps: list[str],
) -> Chapter:
    from model_forensic.reports.study_book import Chapter, box

    c = Chapter("conclusions", "Conclusions", P3)
    c.body.append(box("verdict", _e(head)))
    c.body.append(f"<h3>The baseline ({_e(base.label)})</h3>")
    c.body.append(
        "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in bstudy.conclusions) + "</ul>"
        if bstudy.conclusions
        else "<p>Nothing beyond the expected: losses fall, σ balanced, the config matches the logs.</p>"
    )
    for label, lines in per_run:
        c.body.append(f"<h3>{_e(label)} against the baseline</h3>")
        c.body.append(
            "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in lines) + "</ul>"
            if lines
            else "<p>No difference beyond the noise.</p>"
        )
    items: dict[str, list[str]] = {}
    for r in evid:
        kn = r.knowledge
        if kn is None:
            continue
        lines = [f"Metric {g} set aside ({e})" for g, e in kn.ignore_metrics]
        lines += [f"Class {x} set aside ({e})" for x, e in kn.ignore_classes]
        lines += [f"{t} ({e})" for t, e in kn.caveats]
        for ln in lines:
            items.setdefault(ln, []).append(r.label)
    if items:
        c.body.append("<h3>Read with the team's knowledge</h3><ul>")
        everyone = len(evid)
        c.body += [
            f"<li>{_e(ln)}" + ("" if len(who) == everyone else f" — only for {_e(', '.join(who))}") + "</li>"
            for ln, who in items.items()
        ]
        c.body.append("</ul>")
    if steps:
        c.body.append("<h3>What to do next</h3><ol>" + "".join(f"<li>{_e(s)}</li>" for s in steps[:5]) + "</ol>")
    return c
