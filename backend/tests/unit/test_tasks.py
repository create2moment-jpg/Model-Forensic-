"""Application-scoped tasks: deduplicated, dependency-aware, bounded, timed — and the progressive run opening."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.tasks import TaskManager, TaskSpec, load_specs


def _tm(**specs: TaskSpec) -> TaskManager:
    return TaskManager(dict(specs), None, workers=8)


def test_same_kind_and_subject_joins_the_running_task() -> None:
    tm = _tm()
    gate = threading.Event()
    calls = [0]

    def fn(_r):  # type: ignore[no-untyped-def]
        calls[0] += 1
        gate.wait(2)
        return {"ok": True}

    a = tm.submit("k", "run1", fn)
    b = tm.submit("k", "run1", fn)
    assert a is b
    gate.set()
    tm.wait(a, 5)
    assert a.state == "done" and a.result == {"ok": True} and calls[0] == 1
    assert tm.submit("k", "run1", fn) is a  # cacheable + done: returned, not rerun
    c = tm.submit("k", "run1", fn, force=True)
    tm.wait(c, 5)
    assert c is not a and calls[0] == 2


def test_dependencies_run_first_and_a_failed_dependency_explains_itself() -> None:
    tm = _tm()
    order: list[str] = []

    def slow(_r):  # type: ignore[no-untyped-def]
        time.sleep(0.1)
        order.append("dep")

    def boom(_r):  # type: ignore[no-untyped-def]
        raise RuntimeError("backend said no")

    d = tm.submit("dep", "s", slow)
    t = tm.submit("main", "s", lambda _r: order.append("main"), deps=[d])  # type: ignore[func-returns-value]
    assert t.state == "waiting" and t.waiting_on == [d.id]
    tm.wait(t, 5)
    assert order == ["dep", "main"] and t.state == "done"
    f = tm.submit("bad", "s", boom)
    g = tm.submit("after", "s", lambda _r: None, deps=[f])
    tm.wait(g, 5)
    assert f.state == "failed" and "backend said no" in f.error
    assert g.state == "failed" and "needs bad, which failed" in g.error


def test_concurrency_per_kind_is_bounded() -> None:
    tm = _tm(heavy=TaskSpec("heavy", "Heavy", cost="heavy", concurrency=2))
    live, peak = [0], [0]
    lock = threading.Lock()

    def fn(_r):  # type: ignore[no-untyped-def]
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.1)
        with lock:
            live[0] -= 1

    recs = [tm.submit("heavy", f"run{i}", fn) for i in range(6)]
    for r in recs:
        tm.wait(r, 5)
    assert peak[0] == 2 and all(r.state == "done" for r in recs)


def test_cache_state_and_timings_are_recorded(container) -> None:  # type: ignore[no-untyped-def]
    tm = TaskManager({}, container.docs)
    r = tm.wait(tm.submit("k", "s", lambda _r: None, cached=lambda: True), 5)
    assert r.cache == "warm"
    r2 = tm.wait(tm.submit("k", "s2", lambda _r: None, cached=lambda: False), 5)
    assert r2.cache == "cold"
    samples = tm.timings("k")
    assert [s["cache"] for s in samples] == ["warm", "cold"] and all(s["ms"] >= 0 for s in samples)
    assert tm.list(active=True) == [] and len(tm.list(subject="s")) == 1


def test_master_config_loads_and_local_overrides(tmp_path: Path) -> None:
    core = Path(__file__).parents[2] / "src" / "model_forensic" / "defaults" / "tasks.yaml"
    specs = load_specs(core)
    assert specs["run.metrics"].cost in ("heavy", "very_heavy") and specs["run.configs"].light_mode
    assert all(s.why for s in specs.values())  # every cost is explained
    local = tmp_path / "tasks.local.yaml"
    local.write_text("tasks:\n  run.metrics:\n    cost: medium\n")
    assert load_specs(core, local)["run.metrics"].cost == "medium"
    local.write_text("tasks:\n  run.metrics:\n    cost: huge\n")
    with pytest.raises(ValueError, match="cost must be one of"):
        load_specs(core, local)


def test_progressive_run_opening_over_the_api(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    rid = "local:xf-pipe-finetune"  # a pipeline run with stages
    li = api.get(f"/api/v1/runs/{rid}/light").json()
    assert li["id"] and li["run"]["name"] and set(li["cached"]) == {"series", "configs", "artifacts"}
    c0 = api.get(f"/api/v1/runs/{rid}/overview-cached").json()
    assert c0["ready"]["series"] is False  # nothing invented before it is loaded
    tasks = api.post(f"/api/v1/runs/{rid}/load", params={"mode": "full"}).json()["tasks"]
    assert {t["kind"] for t in tasks} >= {"run.metrics", "run.configs", "run.artifacts"}
    assert all(t["cost"] and t["why"] for t in tasks)
    for rec in container.tasks._tasks.values():
        container.tasks.wait(rec, 30)
    after = api.get(f"/api/v1/runs/{rid}/tasks").json()["tasks"]
    assert all(t["state"] == "done" for t in after), [(t["kind"], t["error"]) for t in after]
    c1 = api.get(f"/api/v1/runs/{rid}/overview-cached").json()
    assert c1["ready"]["series"] and c1["metric_keys"]
    again = api.post(f"/api/v1/runs/{rid}/load", params={"mode": "full"}).json()["tasks"]
    assert {t["id"] for t in again} <= {t["id"] for t in after}  # joined, not repeated
    assert api.post(f"/api/v1/runs/{rid}/load", params={"kinds": ["nope"]}).status_code == 422
    assert api.get("/api/v1/tasks/active").json()["count"] == 0


def test_run_id_is_searchable_and_jobs_name_their_run(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    api.post("/api/v1/sync")
    res = api.get("/api/v1/runs", params={"q": "pipe-finetune"}).json()["runs"]
    assert any(r["ref"]["run_id"] == "xf-pipe-finetune" for r in res)
    jid = api.post("/api/v1/runs/local:xf-pipe-finetune/warm").json()["job_id"]
    listed = {j["id"]: j for j in api.get("/api/v1/jobs").json()}
    assert listed[jid]["run"] == "local:xf-pipe-finetune"  # the Runs table derives the warm-up wheel from this
