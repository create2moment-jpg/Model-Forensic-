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
