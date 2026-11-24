"""Switching to a workspace never needs a restart: each workspace says how many runs are indexed, when it was last
synced and whether a sync runs now; a sync of one workspace is never started twice at once."""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from model_forensic.container import Container


def test_backends_report_runs_last_sync_and_syncing(container: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    b = next(x for x in api.get("/api/v1/backends").json() if x["name"] == "local")
    assert b["runs"] > 0 and b["last_sync"] and b["syncing"] is False  # the fixture synced once
    assert container.service.index.counts_by_backend()["local"] == b["runs"]


def test_one_sync_per_workspace_at_a_time(container: Container) -> None:
    svc = container.service
    src = svc.sources["local"]
    calls: list[int] = []
    real = src.list_runs
    gate = threading.Event()

    def slow(q: Any) -> Any:
        calls.append(1)
        gate.wait(5)
        return real(q)

    src.list_runs = slow  # type: ignore[method-assign]
    t = threading.Thread(target=lambda: svc.sync("local"))
    t.start()
    for _ in range(100):
        if "local" in svc.syncing:
            break
        time.sleep(0.01)
    assert "local" in svc.syncing
    assert svc.sync("local") == 0  # the second caller does not start another sync
    gate.set()
    t.join(5)
    assert "local" not in svc.syncing and len(calls) == 1  # one listing: the second sync never ran


def test_the_last_sync_is_shared_through_the_index(container: Container) -> None:
    """A sync by another process (``mf sync``, ``mf demo``) is seen by the server: the time lives in the index."""
    from model_forensic.api.app import create_app

    container.service.last_sync.clear()  # as if this server never synced
    b = next(x for x in TestClient(create_app(container)).get("/api/v1/backends").json() if x["name"] == "local")
    assert b["last_sync"] and b["runs"] > 0
