"""Knowledge from experience: beliefs checked against runs — colours, probability, weighting, honesty."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from model_forensic.knowledge.beliefs import RunFacts, find_metric, other_diffs, pair_outcome, seed_noise, verify
from model_forensic.knowledge.model import BeliefTest

BASE = {"model.layers": 6, "optimizer.lr": 2e-4, "data.batch_size": 8, "seed": 0, "output_dir": "/x"}


def run(i: int, metric: float, **cfg: object) -> RunFacts:
    return RunFacts(f"local:r{i}", f"r{i}", {**BASE, "seed": i, "output_dir": f"/o/{i}", **cfg}, {"val/mAP": metric})


LR_DOWN = BeliefTest(type="config_effect", key="lr", change="increase", metric="val/mAP", expect="down")


def test_clean_consistent_pairs_reach_dark_green() -> None:
    runs = []
    for k in range(5):  # five clean pairs: raising lr lowers mAP by ~5 %
        runs += [
            run(10 * k, 0.50, **{"data.batch_size": 8 + k}),
            run(10 * k + 1, 0.475, **{"data.batch_size": 8 + k, "optimizer.lr": 4e-4}),
        ]
    v = verify(LR_DOWN, runs)
    assert v.colour == "dark_green" and v.p > 0.85 and v.n == 5 and v.contra == 0
    assert all(e.other_diffs == 0 and e.weight == 1.0 for e in v.evidence)
    assert v.next_experiment == ""  # nothing left to settle


def test_contradiction_is_red_and_named() -> None:
    runs = []
    for k in range(4):
        runs += [
            run(10 * k, 0.50, **{"data.batch_size": 8 + k}),
            run(10 * k + 1, 0.55, **{"data.batch_size": 8 + k, "optimizer.lr": 4e-4}),
        ]
    v = verify(LR_DOWN, runs, confidence="high")
    assert v.colour == "red" and v.p < 0.35 and "Strongest counterexample" in v.summary
    assert v.p_data < v.p  # the person's confidence is visible as a (weak) prior


def test_confounded_pairs_weigh_less_and_hopeless_ones_are_skipped() -> None:
    a = run(1, 0.50)
    b = run(2, 0.45, **{"optimizer.lr": 4e-4, "model.layers": 12, "data.batch_size": 16})
    v = verify(LR_DOWN, [a, b])
    assert v.evidence[0].other_diffs == 2 and abs(v.evidence[0].weight - 1 / 3) < 1e-3
    assert v.colour in ("yellow", "green") and v.colour != "dark_green"  # one confounded pair is never enough
    far = run(3, 0.40, **{"optimizer.lr": 9e-4, **{f"k{i}": i for i in range(25)}})
    v2 = verify(LR_DOWN, [a, far])
    assert v2.n == 0 and v2.skipped == 1 and v2.colour == "grey"
    assert other_diffs(a.config, b.config, "optimizer.lr") == 2  # seed / output dir are noise


def test_seed_noise_raises_the_effect_threshold() -> None:
    reps = [run(i, m) for i, m in enumerate([0.50, 0.52, 0.48, 0.51])]  # same config, different seeds
    noise = seed_noise(reps, "val/mAP")
    assert noise is not None and 0.02 < noise < 0.05
    v = verify(LR_DOWN, [*reps, run(9, 0.49, **{"optimizer.lr": 4e-4})])
    assert v.threshold >= 2 * noise and v.no_effect == 1 and v.contra == 0  # −2 % is within the seed noise


def test_set_decrease_no_change_and_loss_behaviour() -> None:
    off, on = run(1, 0.50, **{"loss.uw": False}), run(2, 0.56, **{"loss.uw": True})
    t = BeliefTest(type="config_effect", key="loss.uw", change="set", value="True", metric="val/mAP", expect="up")
    assert verify(t, [on, off]).evidence[0].outcome == "supports"
    dec = BeliefTest(type="config_effect", key="lr", change="decrease", metric="val/mAP", expect="up")
    hi, lo = run(3, 0.45, **{"optimizer.lr": 4e-4}), run(4, 0.50)
    assert verify(dec, [hi, lo]).evidence[0].outcome == "supports"  # lowering lr raised mAP
    same = BeliefTest(type="config_effect", key="lr", metric="val/mAP", expect="no_change")
    assert verify(same, [lo, run(5, 0.501, **{"optimizer.lr": 4e-4})]).evidence[0].outcome == "supports"
    radar = BeliefTest(type="loss_behaviour", family="radar", behaviour="muted")
    rs = [RunFacts(f"r{i}", f"r{i}", {}, {}, {"radar": {"muted": i != 2}}) for i in range(4)]
    v = verify(radar, rs)
    assert v.n == 4 and v.support == 3 and v.contra == 1


def test_untestable_and_helpers() -> None:
    v = verify(None, [run(1, 0.5)])
    assert v.colour == "grey" and "add a test" in v.summary
    stages = {"val/score[class=a,stage=eval_pre]": 1.0, "val/score[class=a,stage=eval_ft]": 2.0}
    assert find_metric(stages, "val/score[class=a]") == 1.0  # deterministic: the last stage key in sort order
    assert find_metric(stages, "val/score[class=a,stage=eval_ft]") == 2.0  # an exact key always wins
    assert find_metric({"val/x": 3.0}, "val/y") is None
    ev = pair_outcome(LR_DOWN, run(2, 0.45, **{"optimizer.lr": 4e-4}), run(1, 0.50))
    assert ev is not None and ev.outcome == "supports"  # either order


def test_api_flow_history_staleness_and_book(container) -> None:
    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    body = {
        "kind": "belief",
        "statement": "More epochs raise the combined score",
        "confidence": "medium",
        "test": {
            "type": "config_effect",
            "key": "trainer.max_epochs",
            "metric": "val/score[class=combined_metric,task=sparse_detection]",
            "expect": "up",
        },
    }
    assert c.put("/api/v1/knowledge/B-epochs", json=body).status_code == 200
    assert (
        c.put("/api/v1/knowledge/B-free", json={"kind": "belief", "statement": "Batch size 16 is best"}).status_code
        == 200
    )
    items = {b["entry"]["id"]: b for b in c.get("/api/v1/beliefs").json()["beliefs"]}
    assert items["B-epochs"]["verdict"] is None and items["B-free"]["testable"] is False
    vocab = c.get("/api/v1/beliefs/vocabulary").json()
    assert any(k["key"] == "trainer.max_epochs" and k["numeric"] for k in vocab["config_keys"]) and vocab["families"]
    job = c.post("/api/v1/beliefs/verify-all").json()["job_id"]
    for _ in range(200):
        j = c.get(f"/api/v1/jobs/{job}").json()
        if j["status"] != "running":
            break
        import time

        time.sleep(0.05)
    assert j["status"] == "done", j
    d = c.get("/api/v1/beliefs/B-epochs").json()
    assert d["latest"]["verdict"]["colour"] in ("dark_green", "green", "yellow", "red", "grey") and not d["stale"]
    assert len(d["latest"]["history"]) == 1
    # editing the test makes the verdict stale until checked again
    body["test"]["expect"] = "down"
    c.put("/api/v1/knowledge/B-epochs", json=body)
    assert c.get("/api/v1/beliefs/B-epochs").json()["stale"] is True
    # the entries tab stays free of beliefs; the knowledge engine ignores them (no effects)
    assert c.get("/api/v1/runs/local:xf-online-baseline/knowledge").status_code == 200
    # the study book checks the belief on its comparison (max_epochs differs: 5 vs 3)
    st = container.studies.create("GT vs online", runs=["local:xf-pipe-finetune"], baseline="local:xf-online-baseline")
    html_path, summary_path = container.study_book(st.id)
    html = html_path.read_text()
    assert "Your experience, checked on this comparison" in html and "More epochs raise the combined score" in html
    assert json.loads(summary_path.read_text())["chapters"]
