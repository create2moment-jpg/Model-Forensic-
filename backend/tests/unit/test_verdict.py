"""The verdict: one sentence, three numbers and one next step — built only from the analyses' own output."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_forensic.application.verdict import compare_verdict, metric_label, pick_baseline, verdict
from model_forensic.container import Container


def test_labels_are_short() -> None:
    assert metric_label("val/NDS") == "NDS"
    assert metric_label("val/score[class=Car,task=sparse_detection]") == "Car score"
    assert metric_label("train/loss") == "train loss"


def test_baseline_is_picked_with_the_reason(container: Container) -> None:
    svc = container.service
    ref, why = pick_baseline(svc, svc.ref("local:baseline-cl4f-s0"))
    assert str(ref) == "local:baseline-cl4f-s2" and "role=baseline" in why
    # no tagged baseline: the previous completed run of the experiment — never the run's own stages
    ref, why = pick_baseline(svc, svc.ref("local:xf-pipe-finetune"))
    assert ref is not None and "previous completed run" in why
    assert str(ref) not in {
        "local:xf-pipe-finetune-train",
        "local:xf-pipe-finetune-eval-pre",
        "local:xf-pipe-finetune-eval-ft",
    }


def test_verdict_states(container: Container) -> None:
    svc, kf = container.service, container.knowledge.folder
    failed = verdict(svc, kf, "local:lr-sweep-1e3")
    assert failed["state"] == "failed" and failed["sentence"].startswith("Failed — ") and "NaN" in failed["sentence"]
    assert failed["next"]["view"] == "logs"
    ok = verdict(svc, kf, "local:baseline-cl4f-s0")
    assert ok["state"] in ("healthy", "attention", "problem")
    assert ok["baseline"]["ref"] == "local:baseline-cl4f-s2"
    nds = next(h for h in ok["headline"] if h["label"] == "NDS")
    assert nds["baseline"] is not None and nds["delta_pct"] is not None
    # a change below the meaningful threshold is within noise: neither good nor bad
    assert nds["noise"] == (abs(nds["delta_pct"]) < 1.0) and (nds["good"] is None) == nds["noise"]
    assert 2 <= len(nds["spark"]) <= 32 and ok["lead"].startswith("NDS ")
    assert len(ok["headline"]) <= 3 and ok["next"] and ok["next"]["text"]
    # every problem named comes from an observation the review shows
    from model_forensic.application.observations import collect

    ids = {o.id for o in collect(svc, kf, "local:baseline-cl4f-s0", "local:baseline-cl4f-s2")}
    assert {p["id"] for p in ok["problems"]} <= ids
    live = verdict(svc, kf, "local:sam-radar-live")
    assert live["sentence"].startswith("Running")


def test_cached_only_never_loads(container: Container) -> None:
    svc = container.service
    svc.drop_cached("local:query-1800-spiky")
    v = verdict(svc, container.knowledge.folder, "local:query-1800-spiky", cached_only=True)
    assert v["state"] == "not_loaded" and not svc.is_cached("series", "local:query-1800-spiky")
    assert v["remote"] is False  # a local run: the page reads it at once


def test_compare_verdict_names_headline_metrics_and_changes(container: Container) -> None:
    v = compare_verdict(container.service, "local:baseline-cl4f-s2", "local:query-1800-spiky")
    assert v["state"] in ("better", "worse", "mixed", "same") and v["judged"] > 0
    assert "num_queries" in v["detail"] and v["sentence"].endswith(".")


def test_routes(container: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    v = api.get("/api/v1/runs/local:baseline-cl4f-s0/verdict").json()
    assert v["sentence"] and v["headline"]
    assert api.get("/api/v1/runs/local:xf-pipe-finetune/verdict", params={"stage": "nope"}).status_code == 400
    st = api.get("/api/v1/runs/local:xf-pipe-finetune/verdict", params={"stage": "train_finetune"}).json()
    assert st["stage"] == "train_finetune"
    c = api.get(
        "/api/v1/compare/verdict", params={"baseline": "local:baseline-cl4f-s2", "candidate": "local:query-1800-spiky"}
    )
    assert c.status_code == 200 and c.json()["sentence"]


def test_cli_verdict(container: Container, monkeypatch: object) -> None:
    import json

    from typer.testing import CliRunner

    from model_forensic.cli import main as cli

    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)  # type: ignore[attr-defined]
    res = CliRunner().invoke(cli.app, ["verdict", "--run", "local:baseline-cl4f-s0", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["baseline"]["ref"] == "local:baseline-cl4f-s2"
    res = CliRunner().invoke(cli.app, ["verdict", "--run", "local:lr-sweep-1e3"])
    assert res.exit_code == 0 and "Failed" in res.stdout and "→" in res.stdout
