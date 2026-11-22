"""Forensic timeline: stages / sub-stages with measured vs estimated durations, current stage, evidence, code diff."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.application.timeline import diff_stats, read_code_diff, timeline
from model_forensic.projects.profile import stage_role_default

PIPE = "local:xf-pipe-finetune"


def test_pipeline_timeline_stages_roles_and_sources(container) -> None:
    tl = timeline(container.service, PIPE)
    r = tl["run"]
    assert r["id"] == PIPE and r["experiment"] == "sparse_detection" and r["user"] and r["status"] == "completed"
    assert r["duration_s"] and r["current_stage"] is None
    roles = {s["name"]: s["role"] for s in tl["stages"]}
    assert roles == {"train_finetune": "training", "evaluate_pretrain": "evaluation", "evaluate_finetune": "evaluation"}
    for s in tl["stages"]:
        assert s["source"] == "backend timestamps" and s["duration_s"] > 0
        assert s["evidence"]["metrics"] > 0 and s["evidence"]["files"] > 0
        start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        for sub in s["sub_stages"]:
            assert sub["estimated"] and "estimated" in sub["source"]
            if sub["start"] is None:  # outside the measured window: not placed, and said so
                assert "not placed" in sub["note"] and sub["duration_s"] is None
                continue
            assert start <= datetime.fromisoformat(sub["start"]) <= datetime.fromisoformat(sub["end"]) <= end
    train = next(s for s in tl["stages"] if s["name"] == "train_finetune")
    assert train["evidence"]["code_diff"] == "code/repo_diff.txt"
    assert any(d["stage"] == "train_finetune" for d in tl["code_diffs"])


def test_single_running_run_has_a_current_stage(container) -> None:
    tl = timeline(container.service, "local:sam-radar-live")
    assert tl["run"]["status"] == "running" and tl["run"]["current_stage"] == "training"
    names = [s["name"] for s in tl["sub_stages"]]
    assert names[0] == "queued · setup" and "training" in names
    q = tl["sub_stages"][0]
    assert q["source"] == "backend timestamps" and not q.get("estimated")
    assert tl["run"]["command"] and tl["run"]["environment"] and tl["run"]["compute"]


def test_code_diff_is_raw_evidence_with_counts(container) -> None:
    d = read_code_diff(container.service, PIPE)
    assert d["available"] and d["stage"] == "train_finetune" and "raw evidence" in d["label"]
    assert d["stats"] == {"files": 2, "additions": 3, "deletions": 2}
    assert "log_sigma" in d["text"] and not d["truncated"]
    none = read_code_diff(container.service, "local:baseline-cl4f-s0")
    assert none["available"] is False and any("repo_diff" in g for g in none["searched"])
    assert diff_stats("--- a\n+++ b\n+x\n-y\n-z\n") == {"files": 1, "additions": 1, "deletions": 2}


def test_default_stage_roles() -> None:
    assert stage_role_default("evaluate_pretrain") == "evaluation"  # 'pretrain' must not make it training
    assert stage_role_default("train_finetune") == "training"
    assert stage_role_default("data_prepare") == "data" and stage_role_default("misc") == "other"


def test_api_and_project_code_diff_location(container, tmp_path: Path) -> None:
    from model_forensic.api.app import create_app
    from model_forensic.projects import ProjectProfile

    api = TestClient(create_app(container))
    tl = api.get(f"/api/v1/runs/{PIPE}/timeline").json()
    assert len(tl["stages"]) == 3
    d = api.get(f"/api/v1/runs/{PIPE}/code-diff", params={"stage": "train_finetune"}).json()
    assert d["available"] and d["path"] == "code/repo_diff.txt"
    # a project that keeps its diff elsewhere says so in its profile — no code change
    container.service.projects = [
        ProjectProfile.model_validate(
            {"id": "p", "workspaces": ["local"], "artifacts": {"code_diff": ["nowhere/*.patch"]}}
        )
    ]
    assert api.get(f"/api/v1/runs/{PIPE}/code-diff").json() == {"available": False, "searched": ["nowhere/*.patch"]}
