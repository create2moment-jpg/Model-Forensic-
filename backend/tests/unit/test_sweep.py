"""A sweep's trials: hyper-parameters and primary metric, ranked in the goal's direction; parsed from Azure records."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from model_forensic.container import Container
from model_forensic.domain.run import Run, RunKind, RunRef
from model_forensic.infrastructure.connectors.azureml.mapper import map_child, objective, trial_params


def test_demo_sweep_ranked_best_first(container: Container) -> None:
    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    v = c.get("/api/v1/runs/local:radar-lr-sweep/sweep").json()
    assert (v["metric"], v["goal"], v["higher_is_better"]) == ("val/NDS", "maximize", True)
    assert v["best"] == "local:radar-lr-sweep-trial-1"
    assert [t["value"] for t in v["trials"][:3]] == [0.612, 0.603, 0.597]
    assert v["trials"][-1]["status"] == "canceled" and v["trials"][-1]["value"] is None  # stopped early
    assert v["varying"] == ["lr", "weight_decay"] and v["trials"][0]["params"] == {"lr": 2e-4, "weight_decay": 0.01}
    assert (
        v["counts"] == {"trials": 6, "completed": 5, "failed": 0, "canceled": 1, "running": 0}
        and v["missing_values"] == []
    )


def test_azure_sweep_records() -> None:
    assert trial_params({"hyperparameters": '{"--lr": 0.0002, "--batch_size": 16}'}) == {
        "lr": "0.0002",
        "batch_size": "16",
    }
    assert trial_params({"hyperparameters": "not json"}) == {} and trial_params({}) == {}
    job = SimpleNamespace(objective=SimpleNamespace(primary_metric="val/NDS", goal=SimpleNamespace(value="Maximize")))
    assert objective(job) == {"objective.primary_metric": "val/NDS", "objective.goal": "maximize"}
    assert objective(SimpleNamespace()) == {}
    sweep = Run(ref=RunRef(backend="az", run_id="sw"), name="sw", kind=RunKind.SWEEP)
    t = map_child(
        {"runId": "sw_3", "status": "Completed", "tags": {"hyperparameters": '{"lr": 1e-4}'}}, "az", sweep, "sw_3"
    )
    assert t.params == {"lr": "0.0001"} and t.parent == sweep.ref and t.description.startswith("trial")
