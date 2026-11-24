"""Paste anything: a run id, <workspace>:<id>, an Azure ML studio link or a local folder opens the run it names."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.application.resolve import resolve
from model_forensic.container import Container


def test_ids_refs_and_links(container: Container) -> None:
    svc = container.service
    assert resolve(svc, "baseline-cl4f-s0")["ref"] == "local:baseline-cl4f-s0"
    assert resolve(svc, "  local:baseline-cl4f-s0 ")["ref"] == "local:baseline-cl4f-s0"
    link = "https://ml.azure.com/runs/baseline-cl4f-s0?wsid=/subscriptions/x/resourcegroups/y/workspaces/z"
    r = resolve(svc, link)
    assert r["ref"] == "local:baseline-cl4f-s0" and r["how"] == "Azure ML studio link"
    assert (
        resolve(svc, "https://ml.azure.com/experiments/id/abc/runs/baseline-cl4f-s2")["ref"] == "local:baseline-cl4f-s2"
    )
    assert resolve(svc, "https://ml.azure.com/runs/nope-123")["kind"] == "none"
    assert resolve(svc, "local:does-not-exist")["kind"] == "none"
    assert resolve(svc, "")["kind"] == "none"


def test_a_local_folder_not_added_yet_is_offered(container: Container, tmp_path: Path) -> None:
    got = resolve(container.service, str(tmp_path))
    assert got["kind"] == "folder" and got["path"] == str(tmp_path.resolve())
    assert resolve(container.service, str(tmp_path / "missing"))["kind"] == "none"


def test_route(container: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    assert api.get("/api/v1/resolve", params={"text": "baseline-cl4f-s0"}).json()["ref"] == "local:baseline-cl4f-s0"
