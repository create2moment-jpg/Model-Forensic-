"""Notebook bridge: Model Forensic's runs, series, findings and evaluation tables as pandas DataFrames.

    from model_forensic import connect
    mf = connect()                              # same config as `mf` (model-forensic.yaml, config/*.local.yaml)
    mf.runs("experiment:sparse_detection")      # DataFrame of runs
    mf.series("local:xf-pipe-finetune", "train/loss*")   # long frame: run, key, step, value
    mf.wide("local:xf-pipe-finetune", "train/uloss/*")   # step × key
    mf.findings("local:xf-pipe-finetune")       # deterministic findings
    mf.per_class("local:xf-pipe-finetune")      # ruler-free per-class metrics (evaluation JSONs)
    mf.sequences("local:xf-pipe-finetune")      # per-sequence tables, one DataFrame per file
    mf.noise(["local:seed0", "local:seed1"]), mf.effects([...], "local:candidate")

Read-only, like everything else. pandas is optional: ``pip install 'model-forensic[notebook]'``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError("the notebook bridge needs pandas: pip install 'model-forensic[notebook]'") from exc
    return pd


class Forensic:
    def __init__(self, container: Any) -> None:
        self.c = container
        self.svc = container.service

    # ---------------------------------------------------------------- runs
    def runs(self, query: str = "", limit: int = 500, backend: str | None = None) -> Any:
        res = self.svc.search(query, limit=limit, backend=backend)
        rows = [
            {
                "run": str(r.ref),
                "name": r.display_name or r.name,
                "experiment": r.experiment,
                "status": r.status.value,
                "user": r.user,
                "created_at": r.created_at,
                "duration_h": (r.duration_s or 0) / 3600,
                "commit": r.git.commit,
                "branch": r.git.branch,
                **{f"tag.{k}": v for k, v in r.tags.items()},
                **{f"summary.{k}": v for k, v in r.summary_metrics.items()},
            }
            for r in res.runs
        ]
        return _pd().DataFrame(rows)

    def config(self, run: str) -> Any:
        return _pd().Series(self.svc.flat_config(run), name=run)

    # ---------------------------------------------------------------- metrics
    def series(self, run: str | Sequence[str], pattern: str = "*") -> Any:
        runs = [run] if isinstance(run, str) else list(run)
        rows = []
        for r in runs:
            for s in self.svc.series(r):
                k = str(s.key)
                if fnmatch.fnmatchcase(k, pattern) or fnmatch.fnmatchcase(k.split("[", 1)[0], pattern):
                    raw = s.provenance.get("raw_name")
                    rows += [
                        {
                            "run": r,
                            "key": k,
                            "raw_name": raw,
                            "stage": s.provenance.get("stage"),
                            "step": st,
                            "value": v,
                        }
                        for st, v in zip(s.steps, s.values, strict=True)
                    ]
        return _pd().DataFrame(rows, columns=["run", "key", "raw_name", "stage", "step", "value"])

    def wide(self, run: str, pattern: str = "*") -> Any:
        df = self.series(run, pattern)
        return df.pivot_table(index="step", columns="key", values="value", aggfunc="last") if len(df) else df

    def lr(self, run: str) -> Any:
        s = self.svc.lr_series(run)
        return _pd().Series(list(s.values), index=list(s.steps), name="lr") if s is not None else None

    # ---------------------------------------------------------------- findings & evaluation
    def findings(self, run: str, baseline: str | None = None, analyses: Sequence[str] | None = None) -> Any:
        res = self.svc.analyze(run, baseline, analyses=list(analyses) if analyses else None, persist=False)
        rows = [
            {
                "analysis": r.analysis,
                "severity": f.severity.value,
                "title": f.title,
                "statement": f.statement,
                "label": f.label.value,
                "confidence": f.confidence.value,
                "evidence": [e.statement for e in f.evidence],
            }
            for r in res
            for f in r.findings
        ]
        return _pd().DataFrame(rows)

    def checks(self, run: str) -> Any:
        from model_forensic.analysis.base import AnalysisContext
        from model_forensic.analysis.plugins.study import run_checks

        rd = self.svc._safe_run_data(self.svc.ref(run), with_evals=True, with_config=True, with_lr=True)
        return _pd().DataFrame([asdict(c) for c in run_checks(AnalysisContext(target=rd, registry=self.svc.metrics))])

    def per_class(self, run: str) -> Any:
        rows = []
        for ev in self.svc.eval_json(run):
            for cls, ce in ev.classes.items():
                rows += [{"stage": ev.stage, "class": cls, "metric": m, "value": v} for m, v in ce.metrics.items()]
        return _pd().DataFrame(rows, columns=["stage", "class", "metric", "value"])

    def sequences(self, run: str) -> dict[str, Any]:
        pd = _pd()
        out = {}
        for t in self.svc.eval_tables(run):
            if t.recognized:
                df = pd.DataFrame({"sequence_hash": t.seq, "class_name": t.cls, **t.columns})
                out[t.label] = df.set_index(["sequence_hash", "class_name"])
        return out

    def compare(self, baseline: str, candidate: str) -> Any:
        cmp = self.svc.compare(baseline, candidate)
        return _pd().DataFrame([d.model_dump() for d in cmp.deltas])

    def noise(self, runs: Sequence[str]) -> Any:
        from model_forensic.application.noise import noise_floor

        return _pd().DataFrame([asdict(r) for r in noise_floor(self.svc, runs)])

    def effects(self, baselines: Sequence[str], candidate: str) -> Any:
        from model_forensic.application.noise import effects

        return _pd().DataFrame([asdict(r) for r in effects(self.svc, baselines, candidate)])


def connect(config: str | Path | None = None, sync: bool = False) -> Forensic:
    """Open Model Forensic with the same configuration as the CLI (read-only)."""
    from model_forensic.container import build
    from model_forensic.settings import Settings

    c = build(Settings.load(Path(config) if config else None))
    if sync:
        c.service.sync()
    return Forensic(c)
