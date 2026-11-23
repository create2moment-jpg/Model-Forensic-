"""Local runs: pick a folder by browsing or autocomplete instead of typing the whole path."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.container import Container
from model_forensic.infrastructure.connectors.folder.browse import browse, complete


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    (tmp_path / "experiments" / "run-a" / "lightning_logs").mkdir(parents=True)
    (tmp_path / "experiments" / "run-b").mkdir()
    (tmp_path / "experiments" / "run-b" / "events.out.tfevents.1.host").write_bytes(b"")
    (tmp_path / "experiments" / "notes").mkdir()
    (tmp_path / "experiments" / ".cache").mkdir()
    (tmp_path / "experiments" / "readme.txt").write_text("not a folder")
    return tmp_path


def test_browse_lists_folders_and_marks_runs(tree: Path) -> None:
    got = browse(str(tree / "experiments"))
    assert got["ok"] and got["parent"] == str(tree)
    names = {d["name"]: d["run"] for d in got["dirs"]}
    assert names == {"notes": "", "run-a": "lightning_logs/", "run-b": "TensorBoard"}  # no files, no hidden folders
    assert ".cache" in {d["name"] for d in browse(str(tree / "experiments"), hidden=True)["dirs"]}
    assert not browse(str(tree / "missing"))["ok"]
    assert browse("")["path"] == str(Path.home().resolve())


def test_complete_a_typed_path(tree: Path) -> None:
    base = str(tree / "experiments")
    assert complete(f"{base}/ru") == [f"{base}/run-a/", f"{base}/run-b/"]
    assert complete(f"{base}/RUN-A") == [f"{base}/run-a/"]  # case-insensitive
    assert complete(f"{base}/") == [f"{base}/notes/", f"{base}/run-a/", f"{base}/run-b/"]
    assert complete(f"{base}/.c") == [f"{base}/.cache/"]
    assert complete(f"{tree}/nope/x") == [] and complete("") == []


def test_routes_local_mode_only(container: Container, tree: Path) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    r = api.get("/api/v1/settings/local-runs/browse", params={"path": str(tree / "experiments")})
    assert r.status_code == 200 and len(r.json()["dirs"]) == 3
    c = api.get("/api/v1/settings/local-runs/complete", params={"prefix": f"{tree}/exp"})
    assert c.json()["folders"] == [f"{tree}/experiments/"]
    container.settings.server.mode = "shared"
    assert api.get("/api/v1/settings/local-runs/browse").status_code == 403
    assert api.get("/api/v1/settings/local-runs/complete", params={"prefix": "/"}).status_code == 403
