"""Test Space: cold / warm experiments, percentiles, hypotheses, versioned proposals, reviewed acceptance."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from model_forensic.application.test_space import classify, percentiles

RUN = "local:xf-pipe-finetune"


@pytest.fixture()
def api(container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    monkeypatch.setenv("MF_HOME", str(tmp_path / "home"))
    src = container.service.sources["local"]
    orig = src.get_metric_series

    def slow(*a, **k):  # type: ignore[no-untyped-def]
        time.sleep(0.02)  # a backend call costs something: cold is measurably slower than warm
        return orig(*a, **k)

    src.get_metric_series = slow  # type: ignore[method-assign]
    return TestClient(create_app(container)), container, tmp_path / "home"


def _finish(c, eid: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    rec = c.tasks.find("test.experiment", eid)
    c.tasks.wait(rec, 60)
    return c.test_space.get(eid)


def test_percentiles_and_classes() -> None:
    p = percentiles([float(x) for x in range(1, 101)])
    assert (
        p["n"] == 100
        and p["p50"] == 50.5
        and p["p90"] == pytest.approx(90.1)
        and p["p99"] == pytest.approx(99.0, abs=0.1)
    )
    assert percentiles([])["p50"] is None
    th = {"medium": 150, "heavy": 1500, "very_heavy": 15000}
    assert [classify(x, th) for x in (10, 150, 2000, 20000)] == ["lightweight", "medium", "heavy", "very_heavy"]


def test_experiment_hypothesis_proposal_and_review(api) -> None:  # type: ignore[no-untyped-def]
    client, c, home = api
    exp = client.post("/api/v1/test-space/experiments", json={
        "kind": "run.metrics", "runs": [RUN], "repetitions": 5,
        "hypothesis": {"type": "warm_speedup", "value": 2},
    }).json()  # fmt: skip
    assert exp["state"] == "running"
    done = _finish(c, exp["id"])
    st = done["stats"]
    assert done["state"] == "done" and st["cold"]["n"] == 5 and st["warm"]["n"] == 5
    assert st["cold"]["p50"] > st["warm"]["p50"]  # cold really re-reads the backend
    assert {"p50", "p90", "p99"} <= set(st["cold"])
    assert done["verdict"]["result"] == "supported" and "faster" in done["verdict"]["why"]
    # the measured class (lightweight here) disagrees with the configured one (heavy) → a proposal
    assert c.tasks.spec("run.metrics").cost == "heavy"
    prop = client.post(f"/api/v1/test-space/experiments/{exp['id']}/propose").json()
    assert prop["id"].endswith("run.metrics-v1") and prop["status"] == "proposed"
    assert prop["changes"]["cost"] == {"current": "heavy", "proposed": done["measured_class"]}
    assert prop["evidence"]["cold"]["n"] == 5 and prop["evidence"]["runs"] == [RUN]
    f = home / "config" / "test-space" / "proposals" / f"{prop['id']}.yaml"
    assert f.is_file() and yaml.safe_load(f.read_text())["version"] == 1
    assert not (home / "config" / "tasks.local.yaml").exists()  # nothing changes before a review
    again = client.post(f"/api/v1/test-space/experiments/{exp['id']}/propose").json()
    assert again["version"] == 2  # versioned, never overwritten
    client.post(f"/api/v1/test-space/proposals/{again['id']}/reject", json={"note": "duplicate"})
    rev = client.post(f"/api/v1/test-space/proposals/{prop['id']}/accept", json={"note": "ok"}).json()
    assert rev["status"] == "accepted" and rev["review"]["note"] == "ok"
    local = yaml.safe_load((home / "config" / "tasks.local.yaml").read_text())
    assert local["tasks"]["run.metrics"]["cost"] == done["measured_class"]
    assert local["tasks"]["run.metrics"]["_from_proposal"] == prop["id"]
    assert c.tasks.spec("run.metrics").cost == done["measured_class"]  # reloaded
    assert client.post(f"/api/v1/test-space/proposals/{prop['id']}/accept").status_code == 400
    home_view = client.get("/api/v1/test-space").json()
    assert {p["status"] for p in home_view["proposals"]} == {"accepted", "rejected"}
    assert any(e["id"] == exp["id"] for e in home_view["experiments"])
    assert any(p["kind"] == "run.metrics" for p in home_view["production"])


def test_thin_evidence_and_bad_input_are_refused(api) -> None:  # type: ignore[no-untyped-def]
    client, c, _home = api
    exp = client.post("/api/v1/test-space/experiments", json={"kind": "run.configs", "runs": [RUN], "repetitions": 2,
                                                               "hypothesis": {"type": "cost_class", "expect": "medium"}}).json()  # fmt: skip
    done = _finish(c, exp["id"])
    assert done["verdict"]["result"] in ("supported", "refuted") and "expected medium" in done["verdict"]["why"]
    r = client.post(f"/api/v1/test-space/experiments/{exp['id']}/propose")
    assert r.status_code == 400 and "at least 5" in r.json()["error"]["message"]
    assert client.post("/api/v1/test-space/experiments", json={"kind": "nope", "runs": [RUN]}).status_code == 400
    assert client.post("/api/v1/test-space/experiments", json={"kind": "run.configs"}).status_code == 400  # no runs
    bad = {"kind": "run.configs", "runs": [RUN], "hypothesis": {"type": "cost_class", "expect": "huge"}}
    assert client.post("/api/v1/test-space/experiments", json=bad).status_code == 400
    assert client.post("/api/v1/test-space/proposals/x/maybe").status_code == 422


def test_production_timings_feed_the_view(api) -> None:  # type: ignore[no-untyped-def]
    client, c, _home = api
    client.post(f"/api/v1/runs/{RUN}/load", params={"mode": "full"})
    for rec in list(c.tasks._tasks.values()):
        c.tasks.wait(rec, 30)
    prod = {p["kind"]: p for p in client.get("/api/v1/test-space").json()["production"]}
    m = prod["run.metrics"]
    assert m["cold"]["n"] >= 1 and m["measured_class"] in ("lightweight", "medium", "heavy", "very_heavy")
    assert m["why"] and m["cost"] == "heavy"
