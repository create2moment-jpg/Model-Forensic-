"""Sparse (query-based) detection health: query utilization, duplicates, confidence collapse."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from model_forensic.analysis import signal
from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    Requirement,
    fmt,
)
from model_forensic.domain.findings import Confidence, Severity


class Params(AnalysisParams):
    min_positive_query_ratio: float = 0.005
    max_duplicate_rate: float = 0.1
    confidence_collapse_drop: float = 0.3


class SparseDetection:
    name: ClassVar[str] = "sparse_detection"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Sparse detection health"
    description: ClassVar[str] = (
        "Checks query utilization (positive_query_ratio), duplicate predictions (duplicate_rate), and "
        "confidence collapse (mean_score) for DETR-style sparse detectors, per decoder layer when logged."
    )
    category: ClassVar[str] = "sparse_detection"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Requires the training code to log positive_query_ratio, duplicate_rate and/or mean_score.",
    )
    Params: ClassVar[type[AnalysisParams]] = Params

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, Params) else Params.model_validate(params.model_dump())
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        res = AnalysisResult(analysis=self.name)
        found_any = False

        for key, s in sorted(ctx.target.series.items(), key=lambda kv: str(kv[0])):
            _, v = s.finite()
            if v.size < 4:
                continue
            ks = str(key)
            final = float(np.mean(signal.tail(v, 0.1)))
            if key.name == "positive_query_ratio":
                found_any = True
                if final < p.min_positive_query_ratio:
                    ev = fb.evidence(
                        f"{ks} final ≈ {fmt(final)} (< {p.min_positive_query_ratio:g})",
                        run=run.ref,
                        metric=ks,
                        value=final,
                    )
                    res.findings.append(
                        fb.finding(
                            "Very low positive query ratio",
                            "Few queries receive positive assignments; supervision is extremely sparse.",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.MEDIUM,
                            evidence=[ev],
                            hypotheses=[
                                fb.hypothesis(
                                    "Too many queries for scene object density",
                                    supporting=[ev],
                                    falsifiable_by="Halve num_queries; check recall and positive_query_ratio",
                                ),
                                fb.hypothesis(
                                    "One-to-one matching starves queries; consider auxiliary one-to-many assignment",
                                    supporting=[ev],
                                ),
                            ],
                            key=ks,
                        )
                    )
            elif key.name == "duplicate_rate":
                found_any = True
                if final > p.max_duplicate_rate:
                    ev = fb.evidence(
                        f"{ks} final ≈ {fmt(final)} (> {p.max_duplicate_rate:g})", run=run.ref, metric=ks, value=final
                    )
                    res.findings.append(
                        fb.finding(
                            "High duplicate prediction rate",
                            "Multiple queries predict the same object.",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.MEDIUM,
                            evidence=[ev],
                            key=ks,
                        )
                    )
            elif key.name == "mean_score":
                found_any = True
                peak = float(np.max(v))
                if peak > 0 and (peak - final) / peak >= p.confidence_collapse_drop:
                    ev = fb.evidence(
                        f"{ks} fell from peak {fmt(peak)} to {fmt(final)}",
                        run=run.ref,
                        metric=ks,
                        value=final,
                        baseline_value=peak,
                    )
                    res.findings.append(
                        fb.finding(
                            "Confidence collapse",
                            "Predicted confidence fell sharply from its peak.",
                            severity=Severity.HIGH,
                            confidence=Confidence.MEDIUM,
                            evidence=[ev],
                            key=ks,
                        )
                    )
        if not found_any:
            res.skipped_reason = (
                "no sparse-detection diagnostics logged (positive_query_ratio, duplicate_rate, mean_score)"
            )
        return res


PLUGIN = SparseDetection()
