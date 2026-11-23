"""Every downloaded file of a run is under the run's folder: a pipeline's stages are linked in as stages/<stage>/,
paths are reported relative to the folder, and the folder itself is returned once."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.domain.downloads import DownloadProfile

PIPE = "local:xf-pipe-finetune"


def test_stage_files_are_under_the_run_folder(container) -> None:  # type: ignore[no-untyped-def]
    svc = container.service
    files, _ = svc.download(PIPE, DownloadProfile.CONFIGS)
    root = svc.artifact_store.run_root(svc.ref(PIPE))
    stage_files = [f for f in files if f.artifact.run != svc.ref(PIPE)]
    assert stage_files
    for f in stage_files:
        rel = svc.rel_path(PIPE, f.artifact)
        assert rel.startswith(f"stages/{f.artifact.stage}/")
        assert (root / rel).read_bytes() == f.local_path.read_bytes()  # the link reaches the stage's file
    link = root / "stages" / stage_files[0].artifact.stage  # type: ignore[operator]
    assert link.is_symlink() and not str(link.readlink()).startswith("/")  # relative: the data folder can move
    manifest = json.loads((root / "download_manifest.json").read_text())
    assert all(e["rel_path"] and not e["rel_path"].startswith("/") for e in manifest["files"])
    svc.link_stages(PIPE, files)  # idempotent
    assert svc.rel_path_of(PIPE, PIPE, None, "a/b.txt") == "a/b.txt"


def test_api_returns_the_root_and_relative_paths(container) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(create_app(container))
    job = c.post(f"/api/v1/runs/{PIPE}/download", json={"profile": "configs"}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{job}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    res = j["result"]
    assert res["root"].endswith("xf-pipe-finetune") and all("rel_path" in f for f in res["files"])
    assert any(f["rel_path"].startswith("stages/") for f in res["files"])
    ex = c.post(f"/api/v1/runs/{PIPE}/metrics/export").json()
    assert ex["root"] == res["root"] and not ex["csv_rel"].startswith("/") and ex["csv_rel"].endswith(".csv")
