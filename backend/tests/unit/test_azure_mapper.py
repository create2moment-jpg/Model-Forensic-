from types import SimpleNamespace

from model_forensic.domain.run import RunKind, RunStatus
from model_forensic.infrastructure.connectors.azureml.mapper import map_job, map_status

ns = SimpleNamespace


def test_status_mapping() -> None:
    assert map_status("Completed") == RunStatus.COMPLETED
    assert map_status("NotResponding") == RunStatus.FAILED
    assert map_status("Finalizing") == RunStatus.RUNNING
    assert map_status(None) == RunStatus.UNKNOWN


def test_map_command_job() -> None:
    job = ns(
        name="happy_kiwi_123",
        display_name="train-radar",
        experiment_name="sparse4d",
        type="command",
        status="Failed",
        tags={"role": "train"},
        properties={
            "mlflow.source.git.commit": "abc123",
            "azureml.git.branch": "main",
            "StartTimeUtc": "2026-11-01T10:00:00Z",
            "EndTimeUtc": "2026-11-01T12:30:00Z",
        },
        inputs={"train_data": ns(path="azureml:nuscenes:3", type="uri_folder"), "epochs": 24},
        outputs={"model": ns(path="azureml://datastores/x/paths/m", type="custom_model")},
        creation_context=ns(created_at="2026-11-01T09:58:00Z", created_by="Alex"),
        compute="a100-cluster",
        resources=ns(instance_type="Standard_ND96asr_v4", instance_count=2),
        parameters={},
        command="python train.py",
        environment="azureml:env:1",
        description=None,
        error=ns(message="CUDA out of memory"),
        studio_url="https://ml.azure.com/x",
    )
    r = map_job(job, "azureml")
    assert r.ref.backend == "azureml" and r.ref.run_id == "happy_kiwi_123"
    assert r.status == RunStatus.FAILED and r.kind == RunKind.TRAINING
    assert r.git.commit == "abc123" and r.git.branch == "main"
    assert r.duration_s == 2.5 * 3600
    assert r.datasets[0].name == "train_data" and r.datasets[0].version == "3"
    assert r.models[0].name == "model"
    assert r.failure is not None and r.failure.category == "oom"
    assert r.params["inputs.epochs"] == "24"
    assert r.compute.node_count == 2


def test_job_properties_and_inputs_outputs_as_studio_shows_them() -> None:
    """Every property (secret-looking values hidden) and every input / output with its asset name and version —
    from the job record the listing already has, no extra call."""
    job = ns(
        name="calm_fig_7", display_name=None, experiment_name="ct", type="command", status="Completed", tags={},
        properties={"azureml.git.branch": "main", "_azureml.ComputeTargetType": "amlctrain", "AZURE_SAS_TOKEN": "sv=2026&sig=abc",
                    "db.password": "hunter2"},
        inputs={"train": ns(path="azureml:nuscenes:3", type="uri_folder", mode="ro_mount"),
                "val": ns(path="azureml://locations/x/workspaces/w/data/nuscenes_val/versions/7", type="mltable"),
                "raw": ns(path="azureml://datastores/blob/paths/raw/", type="uri_folder"),
                "epochs": 24, "lr": ns(value="0.0002", type="literal")},
        outputs={"model": ns(path="azureml://datastores/x/paths/m", type="custom_model", mode="upload")},
        creation_context=None, compute=None, resources=None, parameters={}, command="python train.py --epochs 24",
        environment="azureml:train-env:12", description=None, studio_url=None,
    )  # fmt: skip
    run = map_job(job, "azureml")
    assert run.properties["azureml.git.branch"] == "main"
    assert run.properties["AZURE_SAS_TOKEN"] == "•••• (hidden)" and run.properties["db.password"] == "•••• (hidden)"
    io = {x.name: x for x in run.io}
    assert (io["train"].asset, io["train"].version, io["train"].mode) == ("nuscenes", "3", "ro_mount")
    assert (io["val"].asset, io["val"].version, io["val"].type) == ("nuscenes_val", "7", "mltable")
    assert io["raw"].asset is None and io["raw"].path == "azureml://datastores/blob/paths/raw/"
    assert (io["epochs"].type, io["epochs"].value) == ("literal", "24")
    assert (io["lr"].type, io["lr"].value) == ("literal", "0.0002")
    assert io["model"].direction == "output" and io["model"].type == "custom_model"


def test_pipeline_graph_from_the_job_definition() -> None:
    """Each step input bound to another step's output is an edge — raw ARM JSON and SDK entities alike."""
    from model_forensic.infrastructure.connectors.azureml.mapper import pipeline_edges

    raw = {
        "prep": {"inputs": {"raw": {"job_input_type": "uri_folder", "uri": "azureml:raw:1"}}},
        "train": {"inputs": {"data": {"job_input_type": "literal", "value": "${{parent.jobs.prep.outputs.clean}}"},
                             "epochs": {"job_input_type": "literal", "value": "24"}}},
        "eval": {"inputs": {"model": {"value": "${{ parent.jobs.train.outputs.model }}"},
                            "data": ns(path="${{parent.jobs.prep.outputs.clean}}")}},
    }  # fmt: skip
    edges = {(e.source, e.output, e.target, e.input) for e in pipeline_edges(raw)}
    assert edges == {
        ("prep", "clean", "train", "data"),
        ("train", "model", "eval", "model"),
        ("prep", "clean", "eval", "data"),
    }
    assert pipeline_edges({}) == [] and pipeline_edges(None) == []
