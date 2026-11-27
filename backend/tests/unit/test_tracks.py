"""Continuous training tracks: an experiment of a workspace followed over time — each run vs the previous run and the
best so far, with the registry's meaningful change as the noise floor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.application.tracks import TrackService
from model_forensic.container import Container
from model_forensic.domain.run import Run, RunKind, RunRef, RunStatus
from model_forensic.shared.errors import ValidationError

T0 = datetime(2026, 11, 1, 2, tzinfo=UTC)
# nightly NDS (higher is better): up, up, within noise, down (regression), failed, up (new best)
NIGHTLY = [0.50, 0.53, 0.5302, 0.49, None, 0.56]


class FakeCT:
    """A continuous-training workspace: one nightly experiment, plus another experiment that is not tracked."""

    name = "ct-ws"
    index_configs_on_sync = False

    def list_runs(self, query: ListRunsQuery) -> Page:
        out = []
        for i, v in enumerate(NIGHTLY):
            start = T0 + timedelta(days=i, minutes=10)
            out.append(Run(
                ref=RunRef(backend=self.name, run_id=f"nightly-{i}"), name=f"nightly-{i}", experiment="ct-nightly",
                kind=RunKind.TRAINING, status=RunStatus.FAILED if v is None else RunStatus.COMPLETED,
                created_at=T0 + timedelta(days=i), started_at=start, ended_at=start + timedelta(hours=6 + i),
                summary_metrics={} if v is None else {"val/NDS": v, "train/loss": 1.0 - v},
            ))  # fmt: skip
        out.append(Run(ref=RunRef(backend=self.name, run_id="other"), name="other", experiment="ad-hoc",
                       status=RunStatus.COMPLETED, created_at=T0, summary_metrics={"val/NDS": 0.9}))  # fmt: skip
        return Page(items=out)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


@pytest.fixture
def ts(container: Container) -> TrackService:
    src = FakeCT()
    container.service.sources[src.name] = src  # type: ignore[assignment]
    container.service.sync(src.name)
    return TrackService(container.docs, container.service)


def test_define_tracks_and_validation(ts: TrackService) -> None:
    t = ts.create({"workspace": "ct-ws", "experiment": "ct-nightly", "metrics": ["val/NDS", "train/loss"]})
    assert t["title"] == "ct-nightly" and t["id"] == "ct-ws-ct-nightly"
    ts.create({"workspace": "ct-ws", "experiment": "ad-hoc", "title": "Ad hoc"})  # more than one track
    assert [x["title"] for x in ts.all()] == ["Ad hoc", "ct-nightly"]
    with pytest.raises(ValidationError, match="already tracked"):
        ts.create({"workspace": "ct-ws", "experiment": "ct-nightly"})
    with pytest.raises(ValidationError, match="unknown workspace"):
        ts.create({"workspace": "nope", "experiment": "x"})
    with pytest.raises(ValidationError, match="workspace and an experiment"):
        ts.create({"workspace": "ct-ws"})
    ts.update(t["id"], {"title": "Nightly detector"})
    assert ts.get(t["id"])["title"] == "Nightly detector"
    ts.delete(t["id"])
    assert [x["title"] for x in ts.all()] == ["Ad hoc"]


def test_progress_vs_previous_and_best(ts: TrackService) -> None:
    t = ts.create({"workspace": "ct-ws", "experiment": "ct-nightly", "metrics": ["val/NDS", "train/loss"]})
    pr = ts.progress(t["id"])
    runs = {r["name"]: r for r in pr["runs"]}
    assert list(runs) == [f"nightly-{i}" for i in range(6)]  # only this experiment, oldest first
    assert runs["nightly-0"]["vs_previous"] is None
    assert runs["nightly-1"]["vs_previous"]["state"] == "better"
    assert runs["nightly-2"]["vs_previous"]["state"] == "same"  # +0.04 %: within the meaningful change
    assert runs["nightly-3"]["vs_best"]["state"] == "worse"  # below the best so far (nightly-1)
    assert runs["nightly-3"]["vs_best"]["ref"] == "ct-ws:nightly-1"
    assert runs["nightly-4"]["status"] == "failed" and runs["nightly-4"]["vs_previous"] is None
    assert runs["nightly-5"]["vs_previous"]["ref"] == "ct-ws:nightly-3"  # the failed run is skipped
    assert pr["best"] == "ct-ws:nightly-5" and runs["nightly-5"]["best"]
    assert pr["state"] == "best" and "the best so far" in pr["sentence"]
    assert runs["nightly-0"]["run_s"] == 6 * 3600 and runs["nightly-0"]["queue_s"] == 600
    assert pr["health"] == {"runs": 6, "completed": 5, "failed": 1, "running": 0,
                            "median_run_s": 8 * 3600, "latest_run_s": 11 * 3600}  # fmt: skip


def test_latest_failed_or_worse_is_the_state(ts: TrackService) -> None:
    NIGHTLY.append(0.51)  # a seventh night: worse than the best (0.56)
    try:
        t = ts.create({"workspace": "ct-ws", "experiment": "ct-nightly", "metrics": ["val/NDS"]})
        ts.svc.sync("ct-ws")
        pr = ts.progress(t["id"])
        assert pr["state"] == "worse"
        assert "the best is still nightly-5" in pr["sentence"] and "-8.9% on" in pr["sentence"]
    finally:
        NIGHTLY.pop()


def test_tracks_api(ts: TrackService, container: Container) -> None:
    from model_forensic.api.app import create_app

    client = TestClient(create_app(container))
    r = client.post("/api/v1/tracks", json={"workspace": "ct-ws", "experiment": "ct-nightly", "metrics": ["val/NDS"]})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert client.post("/api/v1/tracks", json={"workspace": "ct-ws", "experiment": "ct-nightly"}).status_code == 400
    listed = client.get("/api/v1/tracks?progress=true").json()["tracks"]
    assert listed[0]["state"] == "best" and listed[0]["latest"] == "ct-ws:nightly-5"
    pr = client.get(f"/api/v1/tracks/{tid}").json()
    assert pr["primary"] == "val/NDS" and len(pr["runs"]) == 6
    assert client.delete(f"/api/v1/tracks/{tid}").json() == {"deleted": tid}
