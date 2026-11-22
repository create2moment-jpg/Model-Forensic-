"""Artifact prefetch tasks: plan → edit selection → run; stop keeps work, resume continues, cancel cleans up,
a restart leaves tasks resumable, corrupt or partial files are never reused."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.prefetch import PrefetchManager
from model_forensic.container import Container, build
from model_forensic.infrastructure.demo.synthetic import generate_demo
from model_forensic.settings import BackendConfig, Settings

RUN = "local:baseline-cl4f-s0"
BIG = "outputs/checkpoints/big.bin"
SIZE = 3 * 1024 * 1024


def _container(tmp: Path, root: Path) -> Container:
    c = build(
        Settings(
            data_dir=tmp / ".mf",
            backends=[BackendConfig(kind="local", name="local", options={"root": str(root)})],
            artifact_min_free_gb=0.0,
            knowledge_dir=tmp / "knowledge",
        )
    )
    c.service.sync()
    return c


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runs"
    generate_demo(root)
    big = root / RUN.split(":", 1)[1] / BIG
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(bytes(range(256)) * (SIZE // 256))
    return tmp_path, root


def _slow(c: Container, delay: float = 0.02) -> None:
    """Stream in small, slow chunks so a stop lands mid-file."""
    src = c.service.sources["local"]
    orig = src.open_artifact

    def slow(run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        for chunk in orig(run_id, path, offset):
            for i in range(0, len(chunk), 64 * 1024):
                time.sleep(delay if path == BIG else 0)
                yield chunk[i : i + 64 * 1024]

    src.open_artifact = slow  # type: ignore[method-assign]


def _wait(m: PrefetchManager, tid: str, states: tuple[str, ...], timeout: float = 30) -> dict:  # type: ignore[type-arg]
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = m.view(tid)
        if v["state"] in states:
            return v
        time.sleep(0.05)
    raise AssertionError(f"still {m.view(tid)['state']}")


def test_plan_lists_everything_and_the_selection_is_editable(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    m = PrefetchManager(c.service, c.docs)
    v = m.plan(RUN, "fast_debug")
    assert v["state"] == "planned" and v["files_total"] == len(c.service.artifacts(RUN))
    keys = {i["path"]: i["key"] for i in v["items"]}
    assert not next(i for i in v["items"] if i["path"] == BIG)["selected"]  # a checkpoint is not fast-debug
    assert v["bytes_remaining"] > 0 and v["free_bytes"] and v["eta_s"] is None  # no throughput seen yet → no guess
    v = m.select(v["id"], [keys[BIG]], True)
    assert v["files_selected"] == v["files_total"] - (v["files_total"] - v["files_selected"])
    assert any("over 1 GB" in r for r in v["risks"]) is False and v["bytes_remaining"] >= SIZE
    only = m.select(v["id"], only=[keys[BIG]])
    assert only["files_selected"] == 1


def test_stop_keeps_work_and_resume_continues_after_a_restart(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    _slow(c)
    m = PrefetchManager(c.service, c.docs)
    tid = m.plan(RUN, "fast_debug")["id"]
    big_key = next(i["key"] for i in m.view(tid)["items"] if i["path"] == BIG)
    m.select(tid, [big_key], True)
    m.start(tid)
    t0 = time.monotonic()
    while m.view(tid)["throughput_bps"] == 0 and time.monotonic() - t0 < 10:
        time.sleep(0.02)
    time.sleep(0.2)
    m.stop(tid)
    m.wait(tid)
    v = _wait(m, tid, ("stopped",))
    big = next(i for i in v["items"] if i["key"] == big_key)
    assert v["resumable"] and big["state"] == "partial" and 0 < big["partial_bytes"] < SIZE
    small_done = [i for i in v["items"] if i["selected"] and i["state"] in ("done", "reused")]
    assert small_done  # the small files finished before the stop and are kept
    # the server restarts: a fresh manager on the same store
    c2 = _container(tmp, root)
    m2 = PrefetchManager(c2.service, c2.docs)
    assert m2.view(tid)["state"] == "stopped"
    calls: list[int] = []
    orig = c2.service.sources["local"].open_artifact

    def spy(run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        if path == BIG:
            calls.append(offset)
        return orig(run_id, path, offset)

    c2.service.sources["local"].open_artifact = spy  # type: ignore[method-assign]
    m2.start(tid)
    v = _wait(m2, tid, ("completed", "failed"))
    assert v["state"] == "completed", v["error"]
    assert calls and calls[0] == big["partial_bytes"]  # resumed from the partial, not from zero
    assert len(calls) == 1  # small files were reused, only the big one was fetched
    assert all(i["state"] in ("done", "reused") for i in v["items"] if i["selected"])
    assert (root / RUN.split(":", 1)[1] / BIG).read_bytes() == Path(
        next(i for i in v["items"] if i["key"] == big_key)["local_path"]
    ).read_bytes()  # integrity: identical to the source


def test_running_task_is_resumable_after_a_crash(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    m = PrefetchManager(c.service, c.docs)
    tid = m.plan(RUN, "fast_debug")["id"]
    doc = c.docs.get_raw("prefetch_task", tid)
    c.docs.put("prefetch_task", tid, {**doc, "state": "running"}, summary={"id": tid, "state": "running"})
    m2 = PrefetchManager(c.service, c.docs)  # what a restart does
    v = m2.view(tid)
    assert v["state"] == "stopped" and "restart" in v["note"] and v["resumable"]


def test_cancel_removes_partials_and_corrupt_files_are_refetched(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    _slow(c)
    m = PrefetchManager(c.service, c.docs)
    tid = m.plan(RUN, "fast_debug", paths=[BIG])["id"]
    m.start(tid)
    t0 = time.monotonic()
    while m.view(tid)["throughput_bps"] == 0 and time.monotonic() - t0 < 10:
        time.sleep(0.02)
    m.stop(tid, cancel=True)
    m.wait(tid)
    v = _wait(m, tid, ("cancelled",))
    assert not v["resumable"] and all(i["partial_bytes"] == 0 for i in v["items"])
    art = next(a for a in c.service.artifacts(RUN) if a.path == BIG)
    assert c.service.artifact_store.partial_bytes(art) == 0
    from model_forensic.shared.errors import ValidationError

    with pytest.raises(ValidationError):
        m.start(tid)  # cancelled tasks are not resumed
    # a finished file that is later corrupted is not reused
    tid2 = m.plan(RUN, "fast_debug")["id"]
    m.start(tid2)
    v2 = _wait(m, tid2, ("completed", "failed"))
    victim = next(i for i in v2["items"] if i["state"] == "done")
    vp = Path(victim["local_path"])
    vp.chmod(0o644)
    vp.write_bytes(b"x" * vp.stat().st_size)  # same size, different content: only the hash can tell
    tid3 = m.plan(RUN, "fast_debug")["id"]
    item = next(i for i in m.view(tid3)["items"] if i["key"] == victim["key"])
    assert item["state"] != "reused"  # size/hash no longer match → downloaded again


def test_api_flow(env: tuple[Path, Path]) -> None:
    from model_forensic.api.app import create_app

    tmp, root = env
    c = _container(tmp, root)
    api = TestClient(create_app(c))
    v = api.post("/api/v1/prefetch/plan", json={"run": RUN, "profile": "fast_debug"}).json()
    assert v["state"] == "planned" and v["items"]
    assert api.get("/api/v1/prefetch").json()["tasks"][0]["id"] == v["id"]
    api.post(f"/api/v1/prefetch/{v['id']}/start")
    for _ in range(200):
        s = api.get(f"/api/v1/prefetch/{v['id']}").json()
        if s["state"] not in ("running", "planned"):
            break
        time.sleep(0.05)
    assert s["state"] == "completed" and s["files_remaining"] == 0
    assert api.put(f"/api/v1/prefetch/{v['id']}/selection", json={"only": []}).status_code == 400  # completed
    assert api.delete(f"/api/v1/prefetch/{v['id']}").status_code == 200
    assert api.post("/api/v1/prefetch/plan", json={}).status_code == 422


def test_truncated_cache_files_are_not_reused(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    m = PrefetchManager(c.service, c.docs)
    tid = m.plan(RUN, "fast_debug")["id"]
    m.start(tid)
    v = _wait(m, tid, ("completed", "failed"))
    victim = next(i for i in v["items"] if i["state"] == "done")
    p = Path(victim["local_path"])
    p.chmod(0o644)
    p.write_bytes(b"short")
    art = next(a for a in c.service.artifacts(RUN) if f"{a.run}::{a.path}" == victim["key"])
    assert c.service.artifact_store.cached(art) is None  # the size on disk no longer matches


def test_asking_for_the_same_plan_reopens_it(env: tuple[Path, Path]) -> None:
    tmp, root = env
    c = _container(tmp, root)
    m = PrefetchManager(c.service, c.docs)
    a = m.plan(RUN, "fast_debug")["id"]
    assert m.plan(RUN, "fast_debug")["id"] == a  # not a second planned task
    assert m.plan(RUN, "configs")["id"] != a  # another profile is another plan
    m.start(a)
    _wait(m, a, ("completed", "failed"))
    assert m.plan(RUN, "fast_debug")["id"] != a  # once started, a new plan is a new task
