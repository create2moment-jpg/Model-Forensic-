"""Live checks against a real workspace (``mf doctor --live``): access, speed and how well metric names map.

Budgets follow what the v1 tool measured on a busy workspace: token 5 s (CLI credential ≈ 0.3 s; the managed-
identity probe of the default chain stalls for minutes), first job page 10 s (raw ARM ≈ 1.3 s for 10 jobs).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from model_forensic.application.ports import ListRunsQuery
from model_forensic.domain.metrics import Split
from model_forensic.infrastructure.connectors.naming import canonical_key

BUDGETS_S = {
    "token": 5.0,
    "list_jobs": 10.0,
    "get_run": 10.0,
    "metric_names": 15.0,
    "metric_history": 15.0,
    "files": 30.0,
}


@dataclass
class Check:
    name: str
    ok: bool
    seconds: float | None = None
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def slow(self) -> bool:
        return self.seconds is not None and self.seconds > BUDGETS_S.get(self.name, 1e9)


def _timed(name: str, fn: Callable[[], Any]) -> tuple[Check, Any]:
    t0 = time.perf_counter()
    try:
        out = fn()
        return Check(name, True, time.perf_counter() - t0), out
    except Exception as exc:
        first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
        return Check(name, False, time.perf_counter() - t0, f"{type(exc).__name__}: {first}"), None


def run_checks(source: Any, run_id: str | None = None, credential: Any = None) -> list[Check]:
    checks: list[Check] = []
    cred = credential or getattr(getattr(source, "_ml", None), "_credential", None)
    if cred is not None:
        c, _ = _timed("token", lambda: cred.get_token("https://management.azure.com/.default"))
        c.detail = c.detail or "Azure credential works"
        if not c.ok:
            c.detail += " — run `az login` (and `az account set -s <subscription>`), or set credential/tenant_id"
        checks.append(c)
        if not c.ok:
            return checks
    c, page = _timed("list_jobs", lambda: source.list_runs(ListRunsQuery(page_size=10)))
    if page is not None:
        c.detail = f"{len(page.items)} jobs on the first page"
        c.data = {"jobs": [r.name for r in page.items]}
    checks.append(c)
    if not c.ok:
        return checks
    if run_id is None:
        done = [r for r in page.items if r.status.terminal and r.status.value == "completed"]
        if not done:
            checks.append(Check("pick_run", False, None, "no completed job on the first page — pass --run"))
            return checks
        run_id = done[0].name
    c, run = _timed("get_run", lambda: source.get_run(run_id))
    if run is not None:
        c.detail = f"{run_id}: {run.kind.value}, {len(run.children)} stage(s)"
    checks.append(c)
    if run is None:
        return checks
    targets = [ch.run_id for ch in run.children] or [run_id]
    names: list[str] = []
    for t in targets[:4]:
        c, got = _timed("metric_names", partial(source._raw_metric_names, t))
        if got is not None:
            names += list(got)
            c.detail = f"{t}: {len(got)} metric names"
        checks.append(c)
    if names:
        target_with = next((t for t in targets if source._raw_metric_names(t)), targets[0])
        first = source._raw_metric_names(target_with)[0]
        c, series = _timed("metric_history", lambda: source.get_metric_series(target_with, [canonical_key(first)]))
        if series:
            c.detail = f"{first}: {len(series[0])} points"
        checks.append(c)
        keys = [canonical_key(n) for n in names]
        unmapped = sorted({n for n, k in zip(names, keys, strict=True) if k.split == Split.UNKNOWN})
        checks.append(
            Check(
                "naming",
                len(unmapped) < len(names) / 2,
                None,
                f"{len(names) - len(unmapped)}/{len(names)} names map to a split (train/val); unmapped examples: "
                + (", ".join(unmapped[:8]) or "none"),
                {
                    "unmapped": unmapped[:50],
                    "mapped_examples": {n: str(k) for n, k in list(zip(names, keys, strict=True))[:20]},
                },
            )
        )
    c, files = _timed("files", lambda: source.list_artifacts(targets[0]))
    if files is not None:
        so = sum(1 for f in files if f.path.startswith("stage_output/"))
        c.detail = f"{targets[0]}: {len(files)} files ({so} stage outputs)"
    checks.append(c)
    return checks
