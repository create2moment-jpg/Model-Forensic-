"""Load test: many runs, one very long run, measured through the HTTP API in-process.

    python scripts/loadtest.py --runs 2000 --series 50 --points 100000 [--keep DIR] [--json]

Generates synthetic runs in the local layout, syncs them, then times the operations users wait for and compares
them with the budgets (search < 50 ms, cached run page < 1.5 s, 2,000-point chart of a long series from cache
< 300 ms). Exit code 1 when a budget is missed, so CI can run a small version (``--runs 300 --points 20000``).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BUDGET_MS = {"search": 50.0, "run_page_cached": 1500.0, "chart_cached": 300.0, "runs_list": 300.0}


def make_runs(root: Path, n: int, series: int, points: int) -> str:
    rnd = random.Random(7)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    users = ["alex", "dana", "sam", "kim", "ci-bot"]
    exps = [f"exp-{i}" for i in range(25)]
    for i in range(n):
        d = root / f"run-{i:05d}"
        d.mkdir(parents=True)
        created = t0 + timedelta(minutes=37 * i)
        (d / "run.json").write_text(
            json.dumps(
                {
                    "name": d.name,
                    "display_name": d.name,
                    "experiment": exps[i % len(exps)],
                    "kind": "training",
                    "status": "failed" if i % 41 == 0 else "completed",
                    "created_at": created.isoformat(),
                    "ended_at": (created + timedelta(hours=3)).isoformat(),
                    "user": users[i % len(users)],
                    "tags": {"lr": str(10 ** rnd.uniform(-4, -2))[:7], "group": f"g{i % 50}"},
                    "params": {"seed": str(i % 3)},
                }
            )
        )
        lines = []
        for s in range(0, 2000, 100):
            loss = 2.0 * math.exp(-s / 700) + rnd.random() * 0.05
            lines.append(json.dumps({"key": "train/loss", "step": s, "value": loss}))
        lines.append(json.dumps({"key": "val/NDS", "step": 2000, "value": 0.5 + rnd.random() * 0.1}))
        (d / "metrics.jsonl").write_text("\n".join(lines))
    big = root / "run-long"
    big.mkdir()
    meta = json.loads((root / "run-00001" / "run.json").read_text())
    meta.update(name="run-long", display_name="run-long", status="completed")
    (big / "run.json").write_text(json.dumps(meta))
    with (big / "metrics.jsonl").open("w") as f:
        for k in range(series):
            key = "train/loss" if k == 0 else f"train/loss_{k}"
            for s in range(points):
                v = 2.0 * math.exp(-s / (points / 5)) + 0.01 * ((s * 7919 + k) % 97) / 97
                f.write(f'{{"key": "{key}", "step": {s}, "value": {v:.6f}}}\n')
    return "local:run-long"


def timed(fn, repeat: int = 5) -> tuple[float, float]:
    xs = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    return statistics.median(xs), max(xs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--series", type=int, default=50)
    ap.add_argument("--points", type=int, default=100_000)
    ap.add_argument("--keep", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    from fastapi.testclient import TestClient
    from model_forensic.api.app import create_app
    from model_forensic.container import build
    from model_forensic.settings import BackendConfig, Settings

    work = a.keep or Path(tempfile.mkdtemp(prefix="mf-load-"))
    root = work / "runs"
    results: dict[str, dict[str, float]] = {}
    try:
        t = time.perf_counter()
        if not root.exists():
            long_ref = make_runs(root, a.runs, a.series, a.points)
        else:
            long_ref = "local:run-long"
        results["generate"] = {"ms": (time.perf_counter() - t) * 1000}
        s = Settings(
            data_dir=work / "data",
            backends=[BackendConfig(kind="local", name="local", options={"root": str(root)})],
        )
        c = build(s)
        t = time.perf_counter()
        c.service.sync()
        results["sync"] = {"ms": (time.perf_counter() - t) * 1000}
        api = TestClient(create_app(c))

        def ok(r):  # type: ignore[no-untyped-def]
            assert r.status_code == 200, r.text[:300]
            return r

        for name, q in (("search", "user:dana status:completed"), ("search_text", "exp-7")):
            p50, mx = timed(lambda q=q: ok(api.get("/api/v1/runs", params={"q": q, "limit": 50})))
            results[name] = {"p50_ms": p50, "max_ms": mx}
        p50, mx = timed(lambda: ok(api.get("/api/v1/runs", params={"limit": 200})))
        results["runs_list"] = {"p50_ms": p50, "max_ms": mx}
        t = time.perf_counter()
        ok(api.get(f"/api/v1/runs/{long_ref}"))
        results["run_page_cold"] = {"ms": (time.perf_counter() - t) * 1000}
        p50, mx = timed(lambda: ok(api.get(f"/api/v1/runs/{long_ref}")))
        results["run_page_cached"] = {"p50_ms": p50, "max_ms": mx}
        p50, mx = timed(
            lambda: ok(api.get(f"/api/v1/runs/{long_ref}/metrics", params={"keys": ["train/loss"], "max_points": 2000}))
        )
        results["chart_cached"] = {"p50_ms": p50, "max_ms": mx}
    finally:
        if a.keep is None:
            shutil.rmtree(work, ignore_errors=True)

    failed = [k for k, b in BUDGET_MS.items() if k in results and results[k]["p50_ms"] > b]
    if a.json:
        print(json.dumps({"results": results, "budgets_ms": BUDGET_MS, "failed": failed}, indent=2))
    else:
        print(f"{a.runs} runs + 1 run with {a.series} series x {a.points:,} points")
        for k, v in results.items():
            vals = ", ".join(f"{m} {x:,.1f}" for m, x in v.items())
            budget = f"  (budget {BUDGET_MS[k]:.0f} ms){' ✗' if k in failed else ' ✓'}" if k in BUDGET_MS else ""
            print(f"  {k:<16} {vals}{budget}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
