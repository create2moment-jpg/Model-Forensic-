"""Pipeline stage deltas: what changed between evaluation stages of one pipeline (e.g. pretrain → finetune)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, ClassVar

from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    PlotSpec,
    Requirement,
)
from model_forensic.analysis.plugins.architecture import _tail_mean
from model_forensic.domain.findings import Confidence, EpistemicLabel, Evidence, Severity
from model_forensic.domain.metrics import MetricKey, Split


class StageDeltaParams(AnalysisParams):
    min_rel_change: float = 0.02
    max_listed: int = 8
    deprioritized: tuple[str, ...] = ()  # class names that must not drive the verdict (team policy)


def _fmt_key(k: MetricKey) -> str:
    rest = ",".join(f"{a}={b}" for a, b in k.dims if a not in {"stage", "task"})
    return f"{k.name}[{rest}]" if rest else k.name


class StageDelta:
    name: ClassVar[str] = "stage_delta"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Pipeline stage deltas"
    description: ClassVar[str] = (
        "For pipelines that evaluate after several stages (pretrain → finetune): which metrics, classes and "
        "distance buckets improved or regressed between the first and the last evaluation."
    )
    category: ClassVar[str] = "evaluation"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Compares final values of one run's stages; seed noise is unknown without replicate pipelines.",
    )
    Params: ClassVar[type[AnalysisParams]] = StageDeltaParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, StageDeltaParams) else StageDeltaParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        groups: dict[MetricKey, dict[tuple[int, str], float]] = defaultdict(dict)
        for k, s in ctx.target.series.items():
            d = k.dim_dict
            if "stage" not in d or k.split not in (Split.VAL, Split.TEST, Split.UNKNOWN):
                continue
            base = MetricKey(name=k.name, split=k.split, dims=tuple((a, b) for a, b in k.dims if a != "stage"))
            idx = int(s.provenance.get("stage_index", "0") or 0)
            v = _tail_mean(s, 0.01)
            if math.isfinite(v):
                groups[base][(idx, d["stage"])] = v
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if not groups:
            res.skipped_reason = (
                "no metric logged by more than one pipeline stage (needs a pipeline with ≥2 evaluations)."
            )
            return res
        first_stage = min(min(v) for v in groups.values())[1]
        last_stage = max(max(v) for v in groups.values())[1]
        changes = []
        for k, vals in groups.items():
            (_, a), (_, b) = min(vals.items())[0], max(vals.items())[0]
            va, vb = vals[min(vals)], vals[max(vals)]
            hib = ctx.registry.lookup(k.name).higher_is_better
            if hib is None or va == 0:
                continue
            rel = (vb - va) / abs(va)
            good = rel if hib else -rel
            changes.append((good, k, va, vb, a, b))
        if not changes:
            res.skipped_reason = "stage metrics have no known direction (register them in the metric registry)."
            return res
        depri = {c.lower() for c in p.deprioritized}

        def is_depri(k: MetricKey) -> bool:
            return k.dim_dict.get("class", "").lower() in depri

        worse = sorted((c for c in changes if c[0] < -p.min_rel_change), key=lambda c: c[0])
        better = sorted((c for c in changes if c[0] > p.min_rel_change), key=lambda c: -c[0])
        prio_worse = [c for c in worse if not is_depri(c[1])]
        title_stages = f"{first_stage} → {last_stage}"
        summary = fb.evidence(
            f"{len(changes)} metrics evaluated in several stages ({title_stages}): {len(better)} better, "
            f"{len(worse)} worse by more than {p.min_rel_change:.0%}",
            run=run.ref,
            metric="val/*[stage=*]",
            data={"better": len(better), "worse": len(worse), "total": len(changes)},
        )
        res.plots.append(
            PlotSpec(
                id="stage_delta.changes",
                title=f"Change {title_stages} (positive = better)",
                kind="bar",
                x_label="metric",
                y_label="relative change (direction-aware)",
                series=[{"name": "change", "points": [[_fmt_key(c[1]), c[0]] for c in (better[:10] + worse[:10])]}],
            )
        )

        def ev(c: tuple[float, MetricKey, float, float, str, str]) -> Evidence:
            good, k, va, vb, a, b = c
            return fb.evidence(
                f"{_fmt_key(k)}: {a} {va:.4g} → {b} {vb:.4g} ({good:+.1%}, direction-aware)",
                run=run.ref,
                metric=str(k),
                value=vb,
                baseline_value=va,
            )

        if prio_worse:
            evs = [summary] + [ev(c) for c in prio_worse[: p.max_listed]]
            worst = prio_worse[0]
            res.findings.append(
                fb.finding(
                    f"{len(prio_worse)} metric(s) got worse from {first_stage} to {last_stage}",
                    f"The later stage regressed {_fmt_key(worst[1])} by {-worst[0]:.1%}"
                    + (f" and {len(prio_worse) - 1} more" if len(prio_worse) > 1 else "")
                    + ".",
                    severity=Severity.HIGH if worst[0] < -0.1 else Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            f"{last_stage} data/objective shifts the model away from these classes or ranges",
                            supporting=evs[1:2],
                        ),
                        fb.hypothesis(
                            "Catastrophic forgetting during the later stage (learning rate, frozen layers)",
                            supporting=evs[1:2],
                        ),
                    ],
                    key="worse",
                )
            )
        if better:
            res.findings.append(
                fb.finding(
                    f"{len(better)} metric(s) improved from {first_stage} to {last_stage}",
                    f"Largest gain: {_fmt_key(better[0][1])} {better[0][0]:+.1%}.",
                    severity=Severity.INFO,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[summary] + [ev(c) for c in better[: p.max_listed]],
                    key="better",
                )
            )
        depri_worse = [c for c in worse if is_depri(c[1])]
        if depri_worse:
            res.notes.append(
                f"{len(depri_worse)} regression(s) only in de-prioritised classes "
                f"({', '.join(sorted({c[1].dim_dict.get('class', '') for c in depri_worse}))}) — not counted."
            )
        return res


STAGE_DELTA = StageDelta()


# --------------------------------------------------------------------------------------------- per-sequence tables
class SequenceParams(AnalysisParams):
    fp_per_gt: float = 1.0  # false positives per ground-truth object above which a class is precision-poor
    concentration_k: int = 3  # worst sequences per class looked at
    concentration_share: float = 0.4  # share of a class' misses (FN) in its worst k sequences → concentrated
    scene_quantile: float = 0.25  # bottom quantile per class
    scene_class_share: float = 0.7  # sequence in the bottom quantile for this share of classes → scene-level failure
    min_regression: float = 0.05  # per-sequence AP drop vs baseline worth listing
    deprioritized: tuple[str, ...] = ()


class SequenceDrilldown:
    name: ClassVar[str] = "sequence_drilldown"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Per-sequence evaluation drill-down"
    description: ClassVar[str] = (
        "From the per-sequence evaluation parquets (recognised by schema): precision-poor classes, failures "
        "concentrated in a few sequences, scenes that fail for every class, and — with a baseline — the "
        "sequences that regressed."
    )
    category: ClassVar[str] = "evaluation"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.EVAL_TABLES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Sequence hashes are opaque: map them to scenes (weather, time of day) with your dataset index.",
    )
    Params: ClassVar[type[AnalysisParams]] = SequenceParams

    @staticmethod
    def _detection(tables: Sequence[Any]) -> Any:
        det = [t for t in tables if getattr(t, "kind", "") == "detection"]
        return det[-1] if det else None  # pipelines: the last evaluation stage

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        import numpy as np

        from model_forensic.analysis import sequences as sq

        p = params if isinstance(params, SequenceParams) else SequenceParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        tables = list(ctx.target.tables)
        for t in tables:
            if not t.recognized:
                res.notes.append(f"{t.label}: {t.note}")
        t = self._detection(tables)
        if t is None:
            res.skipped_reason = (
                "no recognised per-sequence detection table (evaluation *.parquet with a "
                "(sequence_hash, class_name) index)" + (f"; {len(tables)} file(s) inspected" if tables else "")
            )
            return res
        depri = {c.lower() for c in p.deprioritized}
        rows = sq.per_class(t)
        src = f"{t.label}"
        table_ev = fb.evidence(
            f"{src}: {len(t.sequences())} sequences × {len(rows)} classes; per class AP / precision / recall / FP per GT: "
            + "; ".join(
                f"{r['class']} {r.get('AP') or 0:.2f}/{r.get('precision') or 0:.2f}/{r.get('recall') or 0:.2f}/"
                f"{r.get('fp_per_gt') or 0:.2f}"
                for r in rows
            ),
            run=run.ref,
            artifact=t.path,
            data={"per_class": rows},
        )
        res.plots.append(
            PlotSpec(
                id="sequences.ap",
                title="Mean per-sequence AP per class",
                kind="bar",
                x_label="class",
                y_label="AP",
                series=[{"name": "AP", "points": [[r["class"], r.get("AP")] for r in rows]}],
            )
        )

        poor = [r for r in rows if (r.get("fp_per_gt") or 0) > p.fp_per_gt and r["class"].lower() not in depri]
        if poor:
            res.findings.append(
                fb.finding(
                    f"Precision-poor: {', '.join(r['class'] for r in poor)}",
                    "More false positives than ground-truth objects ("
                    + ", ".join(
                        f"{r['class']} {r['fp_per_gt']:.1f} FP/GT, precision {r['precision']:.0%}" for r in poor
                    )
                    + "): the score threshold or classification head is not separating these classes.",
                    severity=Severity.HIGH if max(r["fp_per_gt"] for r in poor) > 2 else Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table_ev],
                    hypotheses=[
                        fb.hypothesis(
                            "Confusion with a visually similar class (check the confusion pairs)", supporting=[table_ev]
                        ),
                        fb.hypothesis(
                            "Label noise / missing labels for this class in the evaluation set", supporting=[table_ev]
                        ),
                    ],
                    key="precision_poor",
                )
            )

        conc = []
        if "num_fn" in t.columns:
            fn = t.columns["num_fn"]
            for c in t.classes():
                if c.lower() in depri:
                    continue
                m = t.cls == c
                total = float(np.nansum(fn[m]))
                n_seq = int(m.sum())
                if total <= 0 or n_seq < 3 * p.concentration_k:
                    continue
                top = np.sort(fn[m][np.isfinite(fn[m])])[::-1][: p.concentration_k]
                share = float(top.sum()) / total
                if share >= p.concentration_share:
                    worst = sq.worst(t, "AP", p.concentration_k, cls=c)
                    conc.append((c, share, worst))
        if conc:
            evs = [
                fb.evidence(
                    f"{c}: {share:.0%} of missed objects in {p.concentration_k} of {int((t.cls == c).sum())} sequences ("
                    + ", ".join(f"{w['sequence'][:12]} AP {w['AP']:.2f}" for w in ws)
                    + ")",
                    run=run.ref,
                    artifact=t.path,
                    data={"worst": ws},
                )
                for c, share, ws in conc
            ]
            res.findings.append(
                fb.finding(
                    f"Misses concentrated in a few sequences ({', '.join(c for c, _, _ in conc)})",
                    "A handful of sequences account for a large share of the missed objects — look at those scenes "
                    "before changing the model.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "These sequences share a condition (night, rain, sensor dropout, calibration)",
                            supporting=evs[:1],
                        )
                    ],
                    key="concentrated",
                )
            )

        ap = t.columns.get("AP")
        if ap is not None:
            bottom: dict[str, int] = {}
            n_cls = 0
            for c in t.classes():
                if c.lower() in depri:
                    continue
                m = (t.cls == c) & np.isfinite(ap)
                if m.sum() < 8:
                    continue
                n_cls += 1
                thr = float(np.quantile(ap[m], p.scene_quantile))
                for s in t.seq[m & (ap <= thr)]:
                    bottom[str(s)] = bottom.get(str(s), 0) + 1
            scenes = sorted(
                (s for s, k in bottom.items() if n_cls and k / n_cls >= p.scene_class_share), key=lambda s: -bottom[s]
            )
            if n_cls >= 2 and scenes:
                ev = fb.evidence(
                    f"{len(scenes)} sequence(s) are in the bottom {p.scene_quantile:.0%} for at least "
                    f"{p.scene_class_share:.0%} of the classes: " + ", ".join(s[:12] for s in scenes[:10]),
                    run=run.ref,
                    artifact=t.path,
                    data={"sequences": scenes},
                )
                res.findings.append(
                    fb.finding(
                        f"{len(scenes)} scene(s) fail for every class",
                        "Every class is weak in the same sequences: a scene-level cause (conditions, sensor, calibration, "
                        "labels) rather than a class-specific one.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev],
                        hypotheses=[
                            fb.hypothesis(
                                "Out-of-distribution conditions underrepresented in training", supporting=[ev]
                            ),
                            fb.hypothesis("Sensor or calibration problems in these recordings", supporting=[ev]),
                        ],
                        key="scenes",
                    )
                )

        bt = self._detection(ctx.baseline.tables) if ctx.baseline is not None else None
        ref_label = "the baseline"
        if bt is None:  # pipelines: compare the last evaluation stage with the first one
            det = [x for x in tables if x.kind == "detection" and x.stage and x.stage != t.stage]
            if det:
                bt, ref_label = det[0], det[0].stage
        if bt is not None and ap is not None and "AP" in bt.columns:
            base = {(str(s), str(c)): float(v) for s, c, v in zip(bt.seq, bt.cls, bt.columns["AP"], strict=True)}
            drops = []
            for s, c, v in zip(t.seq, t.cls, ap, strict=True):
                b = base.get((str(s), str(c)))
                if (
                    b is not None
                    and np.isfinite(v)
                    and b - float(v) >= p.min_regression
                    and str(c).lower() not in depri
                ):
                    drops.append((b - float(v), str(s), str(c), b, float(v)))
            drops.sort(reverse=True)
            by_cls: dict[str, int] = {}
            for d in drops:
                by_cls[d[2]] = by_cls.get(d[2], 0) + 1
            most = max(by_cls, key=lambda c: by_cls[c]) if by_cls else "-"
            common = len(set(base) & {(str(s), str(c)) for s, c in zip(t.seq, t.cls, strict=True)})
            if drops:
                ev = fb.evidence(
                    f"Largest per-sequence AP drops vs {ref_label}: "
                    + "; ".join(f"{s[:12]} {c} {b:.2f}→{v:.2f}" for _, s, c, b, v in drops[:10]),
                    run=run.ref,
                    artifact=t.path,
                    data={"drops": [d[1:] for d in drops[:50]], "pairs_compared": common},
                )
                res.findings.append(
                    fb.finding(
                        f"{len(drops)} sequence×class pairs regressed vs {ref_label}",
                        f"Out of {common} sequence×class pairs evaluated in both, {len(drops)} lost ≥ "
                        f"{p.min_regression:.2f} AP; most in {most}.",
                        severity=Severity.MEDIUM if len(drops) < 0.2 * max(common, 1) else Severity.HIGH,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev],
                        key="seq_regress",
                    )
                )
        if not res.findings:
            res.notes.append("No precision-poor classes, concentrated misses or scene-level failures.")
        return res


SEQUENCE_DRILLDOWN = SequenceDrilldown()


# --------------------------------------------------------------------------------------------- metric coverage
class CoverageParams(AnalysisParams):
    # stage kind → {glob on the canonical key: label}; the team policy (config/team.local.yaml) replaces this.
    expected: dict[str, dict[str, str]] = {"training": {"train/loss": "total loss"}}


class MetricCoverage:
    name: ClassVar[str] = "metric_coverage"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Metric coverage (logging gaps)"
    description: ClassVar[str] = (
        "Checks every stage against the metric families your team expects it to log (expected_metrics in the team "
        "policy), so a family that was never logged is not mistaken for a model result."
    )
    category: ClassVar[str] = "data"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = ("Stage kind is inferred from the metrics a stage logged.",)
    Params: ClassVar[type[AnalysisParams]] = CoverageParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        import fnmatch

        p = params if isinstance(params, CoverageParams) else CoverageParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        by_stage: dict[str, set[str]] = defaultdict(set)
        for k, s in ctx.target.series.items():
            if "stage" in k.dim_dict:
                continue
            base = MetricKey(name=k.name, split=k.split, dims=k.dims)
            by_stage[s.provenance.get("stage") or "(run)"].add(str(base))
        missing: list[tuple[str, str, list[str]]] = []
        for stage, keys in sorted(by_stage.items()):
            kinds = set()
            if any(x.startswith("train/") for x in keys):
                kinds.add("training")
            if any(x.startswith(("val/", "test/")) for x in keys):
                kinds.add("evaluation")
            for kind in sorted(kinds):
                gaps = [
                    label
                    for pat, label in p.expected.get(kind, {}).items()
                    if not any(
                        fnmatch.fnmatchcase(x, pat) or fnmatch.fnmatchcase(x.split("[", 1)[0], pat) for x in keys
                    )
                ]
                if gaps:
                    missing.append((stage, kind, gaps))
        if not by_stage:
            res.skipped_reason = "the run logged no metrics"
            return res
        if missing:
            evs = [
                fb.evidence(
                    f"{stage} ({kind} stage) logged no: {', '.join(g)}",
                    run=run.ref,
                    data={"stage": stage, "kind": kind, "missing": g},
                )
                for stage, kind, g in missing
            ]
            res.findings.append(
                fb.finding(
                    f"Expected metrics not logged ({sum(len(g) for _, _, g in missing)} families)",
                    "These metric families are expected for this kind of stage but were never logged — conclusions that "
                    "need them cannot be drawn from this run (a logging gap, not a download problem).",
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.OBSERVED,
                    evidence=evs,
                    key="missing",
                )
            )
        else:
            res.notes.append(f"All expected metric families present in {len(by_stage)} stage(s).")
        return res


METRIC_COVERAGE = MetricCoverage()
PLUGINS = (STAGE_DELTA, SEQUENCE_DRILLDOWN, METRIC_COVERAGE)
