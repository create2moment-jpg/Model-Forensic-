"""D4: every analysis becomes an Observation; consumers read only that shape."""

from __future__ import annotations

from itertools import pairwise

from model_forensic.analysis.loss_questions import Answer
from model_forensic.application import observations as om
from model_forensic.domain.observation import Observation

RUN, BASE = "local:cand-radar-8f", "local:baseline-cl4f-s0"


def test_producers_map_each_analysis() -> None:
    a = Answer(
        "features", "features", "Is a module dying?", "bad", "radar 0.57→0.98", ["k1"], "look", ["check dropout"], "why"
    )
    o = om.from_answers([a])[0]
    assert (o.id, o.source, o.cause, o.next_step, o.evidence) == (
        "health:features",
        "health",
        "data",
        "check dropout",
        ("k1",),
    )
    ok = om.from_answers([Answer("diverge", "trajectory", "q", "ok", "fine", ideas=["x"])])[0]
    assert ok.cause == "unknown" and ok.next_step == ""  # only problems carry a cause and a next step
    att = om.from_answers([Answer("attention", "internals", "q", "warn", "a")])[0]
    assert att.source == "internals"
    c = om.from_checks([{"id": "composition", "kind": "composition", "status": "bad", "text": "t", "hypothesis":
                         "implementation", "keys": ["train/loss"], "code_refs": ["d.py:1"]}])[0]  # fmt: skip
    assert c.source == "code" and c.cause == "implementation" and c.code_refs == ("d.py:1",) and c.next_step
    d = om.from_drift({"readings": ["r1"], "rows": [{"key": "train/loss", "outcome": "behind", "sentence": "s",
                                                     "fits": ["optimizer.lr"], "lr_note": ""}]})  # fmt: skip
    assert [x.status for x in d] == ["info", "warn"] and "optimizer.lr" in d[1].next_step


def test_ordering_puts_problems_first() -> None:
    obs = [
        Observation(id=f"x:{s}", source="health", status=s, title="t", statement="s")
        for s in ("ok", "info", "bad", "warn", "na")
    ]
    assert [o.status for o in om.ordered(obs)] == ["bad", "warn", "info", "ok", "na"]
    assert om.counts(obs) == {"bad": 1, "warn": 1, "info": 1, "ok": 1, "na": 1}


def test_collect_and_api(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app
    from model_forensic.cli.main import demo_expectations

    demo_expectations(container.knowledge.folder)
    obs = om.collect(container.service, container.knowledge.folder, RUN, BASE)
    sources = {o.source for o in obs}
    assert {"health", "code", "drift"} <= sources
    assert obs[0].is_problem and all(not (b.is_problem and not a.is_problem) for a, b in pairwise(obs))
    c = TestClient(create_app(container))
    body = c.get(f"/api/v1/runs/{RUN}/observations", params={"baseline": BASE}).json()
    assert body["counts"]["bad"] >= 1 and body["observations"][0]["status"] in ("bad", "warn")
    assert c.get(f"/api/v1/runs/{RUN}/observations", params={"stage": "nope"}).status_code == 400
