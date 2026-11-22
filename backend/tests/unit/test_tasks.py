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


def test_metric_loading_in_background_with_progress_and_single_flight(container) -> None:  # type: ignore[no-untyped-def]
    import threading as th
    import time as tm

    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    calls: list[str] = []
    src = container.service.sources["local"]
    orig = src.get_metric_series

    def slow(run_id, keys=None, on_progress=None):  # type: ignore[no-untyped-def]
        calls.append(run_id)
        tm.sleep(0.2)
        return orig(run_id, keys, on_progress)

    src.get_metric_series = slow  # type: ignore[method-assign]
    runs = ["local:xf-pipe-finetune", "local:baseline-cl4f-s0"]
    tasks = api.post("/api/v1/runs/load-metrics", json={"runs": runs}).json()["tasks"]
    assert {t["kind"] for t in tasks} == {"run.metrics"} and len(tasks) == 2
    # while it runs, a second caller (e.g. the Findings tab) waits for it instead of fetching again
    th.Thread(target=lambda: container.service.series("local:baseline-cl4f-s0")).start()
    for rec in list(container.tasks._tasks.values()):
        container.tasks.wait(rec, 30)
    done = {t["subject"]: t for t in api.get("/api/v1/tasks/active").json()["active"]}
    assert done == {}  # nothing left running
    views = [t for t in container.tasks.list() if t["kind"] == "run.metrics"]
    assert all(t["state"] == "done" for t in views)
    pipe = next(t for t in views if t["subject"] == "local:xf-pipe-finetune")
    assert pipe["progress"]["parts"] >= 2 and pipe["progress"]["done"] == pipe["progress"]["total"] > 0
    assert "metric histories" in pipe["progress"]["step"]
    assert calls.count("baseline-cl4f-s0") == 1  # single flight: fetched once
    assert api.post("/api/v1/runs/load-metrics", json={}).status_code == 422


def test_azure_histories_are_fetched_in_parallel_with_progress() -> None:
    import threading as th
    import time as tm

    from model_forensic.infrastructure.connectors.azureml.connector import AzureMLRunSource, RateLimiter

    live, peak = [0], [0]
    lock = th.Lock()

    class Hist:
        def metric_history(self, run_id, raw):  # type: ignore[no-untyped-def]
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            tm.sleep(0.05)
            with lock:
                live[0] -= 1
            return [0.0, 1.0], [1.0, 0.5]

    src = AzureMLRunSource("ws", None, history=Hist(), jobs=object(), rate=RateLimiter(1000, 1000))  # type: ignore[arg-type]
    names = [f"train_m{i}" for i in range(12)]
    src._raw_metric_names = lambda _rid: names  # type: ignore[method-assign]
    seen: list[tuple[int, int]] = []
    t0 = tm.perf_counter()
    out = src.get_metric_series("job", on_progress=lambda d, t: seen.append((d, t)))
    took = tm.perf_counter() - t0
    assert len(out) == 12 and [s.provenance["raw_name"] for s in out] == names  # order kept
    assert 1 < peak[0] <= src.metric_workers and took < 12 * 0.05
    assert seen[0] == (0, 12) and seen[-1] == (12, 12)
