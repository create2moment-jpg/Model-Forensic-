"""Metric columns end to end: a listing without metric values (like Azure ML) → diagnosis MC-1 → fill → columns.

Smoke test for the Runs table's columns. No metric name is hard-coded: the columns come from the runs' data.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.application.metric_columns import columns
from model_forensic.container import build
from model_forensic.domain.run import Run
from model_forensic.settings import BackendConfig, Settings


def _remote_like(tmp: Path, root: Path):  # type: ignore[no-untyped-def]
    c = build(
        Settings(
            data_dir=tmp / ".mf",
            backends=[BackendConfig(kind="local", name="ws", options={"root": str(root)})],
            artifact_min_free_gb=0.0,
            knowledge_dir=tmp / "knowledge",
        )
    )
    src = c.service.sources["ws"]
    orig = src.list_runs

    def bare(q):  # type: ignore[no-untyped-def]
        page = orig(q)  # the listing has no metric values, as on Azure ML
        return dataclasses.replace(page, items=[r.model_copy(update={"summary_metrics": {}}) for r in page.items])

    src.list_runs = bare  # type: ignore[method-assign]
    for name in ("get_run", "children"):  # nor do single-run lookups in this imitation
        if hasattr(src, name):
            f = getattr(src, name)

            def stripped(*a, _f=f, **k):  # type: ignore[no-untyped-def]
                out = _f(*a, **k)
                if isinstance(out, list):
                    return [r.model_copy(update={"summary_metrics": {}}) for r in out]
                return out.model_copy(update={"summary_metrics": {}})

            setattr(src, name, stripped)
    src.remote = True  # type: ignore[attr-defined]
    c.service.sync()
    return c


def test_empty_columns_are_diagnosed_and_filled(tmp_path: Path, demo_root: Path) -> None:
    from model_forensic.api.app import create_app

    c = _remote_like(tmp_path, demo_root)
    api = TestClient(create_app(c))
    runs = [
        r["ref"]["backend"] + ":" + r["ref"]["run_id"]
        for r in api.get("/api/v1/runs", params={"limit": 6}).json()["runs"]
    ]
    assert runs
    mc = api.post("/api/v1/runs/metric-columns", json={"runs": runs}).json()
    assert mc["runs_with_values"] == 0 and mc["default"] == []  # empty — and said so, not zeros
    d = mc["diagnosis"][0]
    assert (
        d["check"] == "MC-1"
        and d["backend"] == "ws"
        and d["fillable"]
        and "listing carries no metric values" in d["cause"]
    )
    tasks = api.post("/api/v1/runs/summaries", json={"runs": d["missing"]}).json()["tasks"]
    assert {t["kind"] for t in tasks} == {"run.summary"} and all(t["cost"] == "heavy" for t in tasks)
    for rec in list(c.tasks._tasks.values()):
        c.tasks.wait(rec, 60)
    mc2 = api.post("/api/v1/runs/metric-columns", json={"runs": runs}).json()
    assert mc2["runs_with_values"] > 0 and mc2["default"], mc2
    assert all(k["key"] for k in mc2["columns"])
    listed = api.get("/api/v1/runs", params={"limit": 6}).json()["runs"]
    assert any(r["summary_metrics"] for r in listed)  # the Runs table now has values
    assert api.post("/api/v1/runs/summaries", json={}).status_code == 422


def test_ranking_and_headline_without_hard_coded_names() -> None:
    from model_forensic.domain.run import RunRef, RunStatus

    def run(i: int, m: dict[str, float]) -> Run:
        return Run(ref=RunRef(backend="b", run_id=f"r{i}"), name=f"r{i}", status=RunStatus.COMPLETED, summary_metrics=m)

    rs = [run(1, {"val/f1": 0.5, "train/loss": 1.0, "val/iou": 0.2}), run(2, {"val/f1": 0.6, "zz": 3.0}), run(3, {})]
    out = columns(rs, remote={"b": False})
    assert out["default"][0] == "val/f1"  # evaluation metrics first, by coverage
    assert out["default"].index("val/iou") < out["default"].index("zz") if "zz" in out["default"] else True
    assert out["diagnosis"][0]["fillable"] is False and out["diagnosis"][0]["runs_without_values"] == 1

    class P:
        backends = ["b"]

        class metrics:  # noqa: N801
            headline = "val/iou"

    assert columns(rs, [P()])["default"][0] == "val/iou"  # the project's headline wins
