"""Multiple Azure ML workspaces (backends) and multi-run progression endpoints."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.container import build
from model_forensic.settings import BackendConfig, Settings


@pytest.fixture()
def multi(tmp_path: Path, demo_root: Path) -> TestClient:
    # Second workspace = a subset of the demo runs, so the two indexes differ.
    other = tmp_path / "team-b"
    for rid in ("baseline-cl4f-s0", "lr-sweep-1e3"):
        shutil.copytree(demo_root / rid, other / rid)
    s = Settings(
        data_dir=tmp_path / ".mf",
        artifact_min_free_gb=0.0,
        backends=[
            BackendConfig(kind="local", name="perception", title="Perception (prod)", options={"root": str(demo_root)}),
            BackendConfig(kind="local", name="team-b", title="Team B", options={"root": str(other)}),
            # An unreachable Azure workspace must not take the app down (azure extras are not installed in CI).
            BackendConfig(
                kind="azureml", name="broken", options={"subscription_id": "x", "resource_group": "y", "workspace": "z"}
            ),
        ],
    )
    c = build(s)
    c.service.sync()
    return TestClient(create_app(c))


def test_backends_listing_reports_unavailable_workspace(multi: TestClient) -> None:
    b = {x["name"]: x for x in multi.get("/api/v1/backends").json()}
    assert b["perception"]["available"] and b["perception"]["title"] == "Perception (prod)"
    assert b["team-b"]["available"]
    assert not b["broken"]["available"] and b["broken"]["error"]
    assert b["broken"]["workspace"] == "z"
    assert multi.get("/api/v1/health").json()["backends"] == ["perception", "team-b"]


def test_search_is_scoped_to_selected_workspace(multi: TestClient) -> None:
    all_runs = multi.get("/api/v1/runs", params={"limit": 500}).json()
    team_b = multi.get("/api/v1/runs", params={"backend": "team-b"}).json()
    assert {r["ref"]["backend"] for r in all_runs["runs"]} == {"perception", "team-b"}
    assert {r["name"] for r in team_b["runs"]} == {"baseline-cl4f-s0", "lr-sweep-1e3"}
    # natural-language queries are scoped too
    nl = multi.get("/api/v1/runs", params={"q": "failed training jobs", "backend": "team-b"}).json()
    assert [r["ref"]["backend"] for r in nl["runs"]] == ["team-b"] and nl["notes"]
    assert multi.get("/api/v1/runs", params={"backend": "nope"}).status_code == 404
    # sync of a single workspace
    assert multi.post("/api/v1/sync", params={"backend": "team-b"}).status_code == 200


def test_progression_endpoints(multi: TestClient) -> None:
    runs = ["perception:baseline-cl4f-s0", "perception:sam-radar-live", "team-b:lr-sweep-1e3"]
    info = multi.get("/api/v1/run-info", params={"runs": runs}).json()
    assert [r["status"] for r in info] == ["completed", "running", "failed"]
    keys = multi.get("/api/v1/metric-keys", params={"runs": runs}).json()
    assert keys["train/loss"] == 3 and keys["train/epoch"] == 3
    m = multi.get("/api/v1/runs/perception:sam-radar-live/metrics", params={"keys": ["train/loss"]}).json()[0]
    assert m["timestamps"] and len(m["timestamps"]) == len(m["steps"])
    assert max(m["steps"]) < 6000  # still-running job has logged only part of its schedule
