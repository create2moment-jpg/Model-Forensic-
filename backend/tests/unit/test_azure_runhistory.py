"""Azure connector over the run-history gateway (raw ARM jobs, pipeline stages, metrics, stage outputs)."""

from datetime import UTC, datetime
from types import SimpleNamespace

from model_forensic.domain.metrics import MetricKey
from model_forensic.domain.run import RunKind, RunStatus
from model_forensic.infrastructure.connectors.azureml.connector import AzureMLRunSource, RateLimiter
from model_forensic.infrastructure.connectors.azureml.mapper import map_job
from model_forensic.infrastructure.connectors.azureml.runhistory import (
    RawARMJobs,
    RunHistoryGateway,
    parse_metric_row,
    stage_name,
)
from model_forensic.shared.retry import RetryPolicy

ns = SimpleNamespace


def raw_job(name: str, job_type: str = "Pipeline", status: str = "Completed") -> ns:
    return ns(
        name=name,
        properties=ns(
            display_name=f"{name}-display",
            experiment_name="sparse_detection",
            status=ns(value=status),
            job_type=ns(value=job_type),
            tags={"owner": "team"},
            properties={"azureml.git.branch": "main"},
            description="d",
            compute_id="/subscriptions/s/.../computes/a100",
            services={"Studio": ns(endpoint="https://studio/x")},
        ),
        system_data=ns(created_at=datetime(2026, 11, 1, tzinfo=UTC), created_by="alice"),
    )


class Resp(dict):
    def __init__(self, value, token=None):
        super().__init__(continuationToken=token)
        self.value = value


class FakeHistory:
    _base_url = "https://region.api.azureml.ms"

    def __init__(self):
        self.metric_calls = []
        self.runs = ns(
            get_child_by_experiment_name=lambda *a: [
                {"runId": "pipe_train", "displayName": "train_torch"},
                {"runId": "pipe_eval", "properties": {"azureml.moduleName": "evaluate_torch"}},
            ]
        )
        self.metrics = ns(
            list_metric=self._list_metric, get_sampled_metric=self._sampled, get_full_fidelity_metric=self._full
        )
        self.run_artifacts = ns(list_in_container_by_experiment_name=lambda *a: [{"path": "user_logs/std_log.txt"}])

    def _list_metric(self, sub, rg, ws, run_id, body):
        self.metric_calls.append(body)
        if not body:
            return Resp([{"metricKey": {"name": "train_sparse_detection/d3_loss_cls/unweighted_loss"}}], token="t1")
        return Resp([{"metricKey": {"name": "evaluation/sparse_detection/PassengerCar"}}])

    def _full(self, sub, rg, ws, run_id, body):
        if body["metricName"].startswith("evaluation"):
            raise RuntimeError("HttpResponseError: not supported")  # → sampled fallback
        if "continuationToken" not in body:
            return {"value": ["{'step': 1, 'data': {'v': 0.9}}", {"step": 3, "data": {}}], "continuationToken": "p2"}
        return {"value": [{"step": 2, "data": {"v": 0.5}}]}

    def _sampled(self, sub, rg, ws, run_id, body):
        if body["metricName"].startswith("train"):
            return {
                "value": [{"step": 2, "data": {"v": 0.5}}, "{'step': 1, 'data': {'v': 0.9}}", {"step": 3, "data": {}}]
            }
        return {"value": [{"step": 10, "data": {"v": 0.61}}]}

    def send_request(self, req):
        assert "artifacturi" in req.url and "path=user_logs/std_log.txt" in req.url
        return ns(text=lambda: '"https://acct.blob.core.windows.net/c/f?sig=x"')


class FakeContainer:
    def list_blobs(self, name_starts_with):
        p = name_starts_with
        return [ns(name=p, size=0), ns(name=p + "summaries/train/composed_config.yaml", size=24, last_modified=None)]

    def download_blob(self, name):
        assert name == "azureml/pipe_train/stage_output/summaries/train/composed_config.yaml"
        return ns(readall=lambda: b"optimizer:\n  lr: 0.0002\n")


class FakeOps:
    def __init__(self, jobs):
        self.jobs = {j.name: j for j in jobs}
        self.calls = []

    def list(self, rg, ws, **kw):
        self.calls.append(kw)
        return list(self.jobs.values())

    def get(self, rg, ws, name):
        return self.jobs[name]


def source() -> tuple[AzureMLRunSource, FakeHistory, FakeOps]:
    hist = FakeHistory()
    ops = FakeOps([raw_job("pipe"), raw_job("pipe_train", "Command")])
    gw = RunHistoryGateway(ns(jobs=None), "sub", "rg", "ws", history=hist, container=FakeContainer())
    src = AzureMLRunSource(
        "azureml",
        ns(jobs=None),
        history=gw,
        jobs=RawARMJobs(ops, "rg", "ws"),
        rate=RateLimiter(1000, 1000),
        policy=RetryPolicy(max_attempts=1),
    )
    return src, hist, ops


def test_map_raw_arm_job() -> None:
    run = map_job(raw_job("pipe"), "azureml")
    assert (run.display_name, run.experiment, run.kind, run.status) == (
        "pipe-display",
        "sparse_detection",
        RunKind.PIPELINE,
        RunStatus.COMPLETED,
    )
    assert run.user == "alice" and run.created_at and run.git.branch == "main" and run.compute.target == "a100"
    assert run.backend_extras["studio_url"] == "https://studio/x"


def test_listing_uses_raw_arm_active_only() -> None:
    src, _, ops = source()
    from model_forensic.application.ports import ListRunsQuery

    page = src.list_runs(ListRunsQuery(page_size=10))
    assert {r.name for r in page.items} == {"pipe", "pipe_train"}
    assert ops.calls[0]["list_view_type"] == "ActiveOnly"


def test_pipeline_children_are_stages() -> None:
    src, _, _ = source()
    run = src.get_run("pipe")
    assert [c.run_id for c in run.children] == ["pipe_train", "pipe_eval"]
    assert (
        "pipe_train=train_torch" in run.backend_extras["stages"]
        and "pipe_eval=evaluate_torch" in run.backend_extras["stages"]
    )


def test_metrics_follow_continuation_and_map_xflow_names() -> None:
    src, hist, _ = source()
    keys = src.list_metric_keys("pipe_train")
    assert len(hist.metric_calls) == 2 and hist.metric_calls[1] == {"continuationToken": "t1"}
    assert MetricKey.parse("train/uloss/cls[layer=3,task=sparse_detection]") in keys
    series = {str(s.key): s for s in src.get_metric_series("pipe_train")}
    loss = series["train/uloss/cls[layer=3,task=sparse_detection]"]
    assert loss.steps == (1.0, 2.0) and loss.values == (0.9, 0.5)  # sorted; empty row dropped
    assert loss.provenance["raw_name"] == "train_sparse_detection/d3_loss_cls/unweighted_loss"
    assert series["val/score[class=PassengerCar,task=sparse_detection]"].values == (0.61,)


def test_artifacts_include_stage_outputs_and_config(monkeypatch) -> None:
    src, _, _ = source()
    paths = {a.path for a in src.list_artifacts("pipe_train")}
    assert paths == {"user_logs/std_log.txt", "stage_output/summaries/train/composed_config.yaml"}
    cfg = b"".join(src.open_artifact("pipe_train", "stage_output/summaries/train/composed_config.yaml"))
    assert b"lr: 0.0002" in cfg
    snaps = src.get_configs("pipe_train")
    assert any(s.data.get("optimizer", {}).get("lr") == 0.0002 for s in snaps)

    import io

    import model_forensic.infrastructure.connectors.azureml.runhistory as rh

    monkeypatch.setattr(rh, "urlopen", lambda url, timeout: io.BytesIO(b"log line\n"))
    assert b"".join(src.open_artifact("pipe_train", "user_logs/std_log.txt")) == b"log line\n"


def test_helpers() -> None:
    assert stage_name({"runId": "a/b/c"}) == "c"
    assert parse_metric_row("not a dict") is None
    assert parse_metric_row({"data": {"x": "1.5"}}) == (-1.0, 1.5)
