"""Starred experiments: first in the list, and `exp:starred` (or the Runs filter) shows only their runs."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_forensic.container import Container


def test_star_filter_and_order(container: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    exps = api.get("/api/v1/experiments").json()
    target = exps[-1]["name"]  # star the last one: it must come first
    assert api.put("/api/v1/experiments/star", params={"name": target, "value": True}).json()["starred"] is True
    exps = api.get("/api/v1/experiments").json()
    assert exps[0]["name"] == target and exps[0]["starred"] is True
    assert all(not e["starred"] for e in exps[1:])
    runs = container.service.search("exp:starred", limit=200).runs
    assert runs and {r.experiment for r in runs} == {target}
    # combines with other filters, survives a sync, and unstars
    assert all(r.status.value == "completed" for r in container.service.search("exp:starred status:completed").runs)
    container.service.sync()
    assert container.service.index.starred_experiments() == {target}
    api.put("/api/v1/experiments/star", params={"name": target, "value": False})
    assert container.service.search("exp:starred").runs == []
    assert api.put("/api/v1/experiments/star", params={"name": " "}).status_code == 422
