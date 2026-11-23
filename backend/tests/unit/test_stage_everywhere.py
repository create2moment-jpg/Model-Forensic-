"""One stage everywhere: the metric catalogue says which series are single values, and the comparison (deltas and
curves) reads one stage in every run that has it — stages are never mixed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app

PIPE, BASE = "local:xf-pipe-finetune", "local:xf-online-baseline"


def test_catalogue_counts_points(container) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(create_app(container))
    cat = c.get(f"/api/v1/runs/{PIPE}/metric-catalog").json()
    by_stage = {}
    for e in cat["entries"]:
        by_stage.setdefault(e["stage"], []).append(e["points"])
    assert all(p <= 2 for p in by_stage["evaluate_finetune"])  # an evaluation stage logs one value per metric
    assert max(by_stage["train_finetune"]) > 10  # training (online validation included) logs curves


def test_compare_reads_one_stage(container) -> None:  # type: ignore[no-untyped-def]
    svc = container.service
    head = {d.metric: d.candidate for d in svc.compare(BASE, PIPE).deltas}
    pre = {d.metric: d.candidate for d in svc.compare(BASE, PIPE, stage="evaluate_pretrain").deltas}
    shared = [m for m in head if m in pre and "mean_f1_score" in m]
    assert shared and any(head[m] != pre[m] for m in shared)  # the pretrain evaluation, not the headline (finetune)
    same = {d.metric: d.candidate for d in svc.compare(BASE, PIPE, stage="nope").deltas}
    assert same == head  # an unknown stage changes nothing
    c = TestClient(create_app(container))
    api = c.get("/api/v1/compare", params={"baseline": BASE, "candidate": PIPE, "stage": "evaluate_pretrain"}).json()
    assert {d["metric"]: d["candidate"] for d in api["deltas"]} == pre


def test_curves_take_a_page_stage_for_the_runs_that_have_it(container) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(create_app(container))
    r = c.post("/api/v1/compare/curves", json={"runs": [BASE, PIPE], "stage": "train_finetune"})
    assert r.status_code == 200, r.text
    st = {x["ref"]: x["stage"] for x in r.json()["runs"]}
    assert st[PIPE] == "train_finetune" and st[BASE] == ""  # the plain run keeps its own metrics
    explicit = c.post("/api/v1/compare/curves", json={"runs": [BASE, PIPE], "stage": "train_finetune",
                                                      "stages": {PIPE: "evaluate_finetune"}}).json()  # fmt: skip
    assert {x["ref"]: x["stage"] for x in explicit["runs"]}[PIPE] == "evaluate_finetune"  # a per-run choice wins


def test_curves_pick_keys_the_stage_has(container) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(create_app(container))
    d = c.post("/api/v1/compare/curves", json={"runs": [BASE, PIPE], "stage": "evaluate_finetune"}).json()
    keys = [k["key"] for k in d["keys"]]
    assert keys and "train/loss" not in keys  # the evaluation stage logged no training loss
    k = d["keys"][0]
    assert len(k["series"]) == 2 and all(len([v for v in s["values"] if v is not None]) <= 2 for s in k["series"])
