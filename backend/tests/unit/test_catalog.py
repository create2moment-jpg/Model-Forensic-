"""Artifact catalog (v1 study_cli catalog/inventory): families by the question they answer, before downloading."""

import time

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def test_catalog_families_and_family_download(container) -> None:
    c = TestClient(create_app(container))
    run = "local:xf-pipe-finetune"
    cat = c.get(f"/api/v1/runs/{run}/catalog").json()
    fams = {f["family"]: f for f in cat["families"]}
    for fam in (
        "driver logs",
        "composed configs",
        "training curves (metrics.csv)",
        "distance-stratified PR",
        "false-positive analysis",
        "per-sequence table",
        "checkpoints",
        "metric histories",
    ):
        assert fam in fams, fam
        assert fams[fam]["answers"] and fams[fam]["caveat"]
    assert "other files" not in fams  # every file of the demo pipeline is explained
    assert sum(f["files"] for f in cat["families"]) == cat["files"]
    assert {"Animal", "Pedestrian"} <= set(fams["false-positive analysis"]["classes"])
    assert set(fams["composed configs"]["stages"]) == {"train_finetune", "evaluate_pretrain", "evaluate_finetune"}
    # download exactly one family by its paths
    paths = fams["composed configs"]["paths"]
    job = c.post(f"/api/v1/runs/{run}/download", json={"profile": "full_forensic", "paths": paths}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and len(j["result"]["files"]) == len(paths)
    after = {f["family"]: f for f in c.get(f"/api/v1/runs/{run}/catalog").json()["families"]}
    assert after["composed configs"]["cached"] == len(paths)
