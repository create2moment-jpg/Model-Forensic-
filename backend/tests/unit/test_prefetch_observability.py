"""Prefetch observability: phases, per-file states, heartbeat, structured events, stalls, diagnosis, honest progress."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.application.prefetch import PHASES, Item, Task, diagnose
from tests.unit.test_prefetch_tasks import BIG, RUN, _container, _slow, _wait, env  # noqa: F401


def _phases(v: dict) -> list[str]:  # type: ignore[type-arg]
    return [e["phase"] for e in v["events"] if e.get("kind") == "phase"]


def test_phases_files_and_progress_are_observable(env: tuple[Path, Path]) -> None:  # noqa: F811
    tmp, root = env
    c = _container(tmp, root)
    m = c.prefetcher if c.prefetcher else None
    from model_forensic.application.prefetch import PrefetchManager

    m = m or PrefetchManager(c.service, c.docs)
    _slow(c, 0.01)
    v = m.plan(RUN, "full_forensic")
    assert v["phase"] == "awaiting_selection" and _phases(v) == ["manifesting", "awaiting_selection"]
    assert "press Start" in v["next"]
    m.start(v["id"])
    seen_states: set[str] = set()
    seen_phases: set[str] = set()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        s = m.view(v["id"])
        seen_phases.add(s["phase"])
        seen_states |= set(s["item_states"])
        if s["in_flight"]:
            f = s["in_flight"][0]
            assert f["state"] in ("downloading", "validating") and f["attempts"] >= 1
        if s["state"] == "running":
            assert s["alive"] in (True, None) and s["next"]
        if s["state"] == "completed":
            break
        time.sleep(0.02)
    assert s["state"] == "completed" and s["phase"] == "completed"
    assert {"downloading"} <= seen_phases and "downloading" in seen_states
    ph = _phases(s)
    assert ph[-3:] == ["downloading", "validating", "completed"] and "queued" in ph
    assert s["progress"]["determinate"] and s["progress"]["fraction"] == 1.0
    assert all(k in s["phase_times"] for k in ("manifesting", "downloading"))
    kinds = {e["kind"] for e in s["events"]}
    assert {"phase", "file", "info"} <= kinds
    big = next(i for i in s["items"] if i["path"] == BIG)
    assert big["state"] == "done" and big["started_at"] and big["ended_at"] and big["bytes_done"] > 0
    ev = m.events(v["id"], after=len(s["events"]) - 2)
    assert len(ev["events"]) == 2 and ev["next"] == len(s["events"])


def test_unknown_sizes_never_get_an_invented_percentage() -> None:
    t = Task("t", "local:r", "r", "fast_debug", [], items=[Item("k1", "local:r", None, "a", "other", 10, True),
                                                       Item("k2", "local:r", None, "b", "other", None, True)])  # fmt: skip
    p = t.summary()["progress"]
    assert p["determinate"] is False and p["fraction"] is None and p["bytes_total"] is None
    assert p["files_selected"] == 2


def test_a_stall_is_detected_named_and_cleared(env: tuple[Path, Path]) -> None:  # noqa: F811
    tmp, root = env
    c = _container(tmp, root)
    from model_forensic.application.prefetch import PrefetchManager

    m = PrefetchManager(c.service, c.docs)
    src = c.service.sources["local"]
    orig = src.open_artifact
    gate = threading.Event()

    def stuck(run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        first = True
        for chunk in orig(run_id, path, offset):
            yield chunk[:1024]
            if first and path == BIG:
                first = False
                gate.wait(10)  # the stream stops answering
            yield chunk[1024:]

    src.open_artifact = stuck  # type: ignore[method-assign]
    v = m.plan(RUN, "full_forensic")
    t = m.get(v["id"])
    t.stall_after_s = 1.0
    m._save(t)
    m.start(v["id"])
    live = m.get(v["id"])
    live.stall_after_s = 1.0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15 and not m.view(v["id"])["stalled"]:
        time.sleep(0.1)
    s = m.view(v["id"])
    assert s["stalled"] and s["alive"]
    d = {x["cause"]: x for x in s["diagnosis"]}
    assert "stalled" in d and BIG in d["stalled"]["evidence"] and d["stalled"]["fix"]
    assert any(e["kind"] == "stall" and BIG in e["files"] for e in s["events"])
    gate.set()
    done = _wait(m, v["id"], ("completed",))
    assert not done["stalled"] and any(e["kind"] == "recovered" for e in done["events"])


def test_diagnosis_from_evidence() -> None:
    now = time.time()
    items = [Item(f"k{i}", "local:r", None, f"f{i}", "other", 2000, True, state="done", started_at=now - 1,
                  ended_at=now - 0.2) for i in range(6)]  # fmt: skip
    items.append(Item("kx", "local:r", None, "big", "other", 10**9, True, state="queued"))
    items.append(Item("ky", "local:r", None, "big2", "other", 10, True, state="queued"))
    t = Task("t", "local:r", "r", "full", [], state="running", phase="downloading", items=items, workers=1,
             retries=3, last_heartbeat=now - 30, throughput_bps=1e5, elapsed_s=60)  # fmt: skip
    t.events.append({"kind": "retry", "text": "retry 1 of big: TimeoutError"})
    causes = {x["cause"]: x for x in diagnose(t, hint_bps=5e6, free_bytes=10**6)}
    assert set(causes) >= {"worker_not_alive", "backend_transient", "low_throughput", "per_file_latency",
                           "single_worker", "disk_space"}  # fmt: skip
    assert "TimeoutError" in causes["backend_transient"]["evidence"]
    assert causes["worker_not_alive"]["severity"] == "critical"
    healthy = Task("h", "local:r", "r", "full", [], state="running", phase="downloading", last_heartbeat=now, workers=4)
    assert diagnose(healthy, None, None) == []
    assert set(PHASES) >= {"planned", "manifesting", "awaiting_selection", "queued", "downloading", "validating",
                           "completed", "stopped", "cancelled", "failed"}  # fmt: skip


def test_retries_are_counted_and_stop_is_resumable_with_states(env: tuple[Path, Path]) -> None:  # noqa: F811
    tmp, root = env
    c = _container(tmp, root)
    from model_forensic.application.prefetch import PrefetchManager

    m = PrefetchManager(c.service, c.docs)
    src = c.service.sources["local"]
    orig = src.open_artifact
    fails = {"n": 1}

    def flaky(run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        if path == BIG and fails["n"]:
            fails["n"] -= 1
            raise ConnectionError("connection reset by peer")
        yield from orig(run_id, path, offset)

    src.open_artifact = flaky  # type: ignore[method-assign]
    c.service.artifact_store.policy = type(c.service.artifact_store.policy)(max_attempts=3, base_delay_s=0.01)
    v = m.plan(RUN, "full_forensic")
    m.start(v["id"])
    s = _wait(m, v["id"], ("completed", "failed"))
    assert s["state"] == "completed" and s["retries"] == 1
    assert any(e["kind"] == "retry" and "connection reset" in e["text"] for e in s["events"])
    assert next(i for i in s["items"] if i["path"] == BIG)["attempts"] == 2


def test_api_exposes_phase_diagnosis_and_events(env: tuple[Path, Path]) -> None:  # noqa: F811
    from model_forensic.api.app import create_app

    tmp, root = env
    c = _container(tmp, root)
    api = TestClient(create_app(c))
    v = api.post("/api/v1/prefetch/plan", json={"run": RUN, "profile": "fast_debug"}).json()
    assert v["phase"] == "awaiting_selection" and "diagnosis" in v and "progress" in v
    api.post(f"/api/v1/prefetch/{v['id']}/start")
    for _ in range(400):
        s = api.get(f"/api/v1/prefetch/{v['id']}").json()
        if s["state"] == "completed":
            break
        time.sleep(0.05)
    ev = api.get(f"/api/v1/prefetch/{v['id']}/events", params={"after": 0}).json()
    assert ev["next"] == len(ev["events"]) and ev["events"][0]["kind"]
