"""Logs: every log of a run and its stages listed, the most telling one first, read as a ranged tail; stages load alone."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.run_logs import log_catalog, read_log_file
from model_forensic.application.stages import stage_view
from model_forensic.container import Container
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.logs import best_log, group_of
from model_forensic.domain.run import RunRef
from model_forensic.infrastructure.connectors.azureml import runhistory as rh

R = RunRef(backend="azureml", run_id="job")


def art(path: str, size: int | None = None) -> Artifact:
    return Artifact(run=R, path=path, size_bytes=size)


def test_the_training_process_log_comes_before_platform_and_other_ranks() -> None:
    logs = [
        art("system_logs/lifecycler/execution-wrapper.log", 9000),
        art("system_logs/cs_capability/cs-capability.log", 9000),
        art("user_logs/std_log_process_1.txt", 9000),
        art("user_logs/std_log_process_0.txt", 4000),
        art("logs/azureml/executionlogs.txt", 100),
    ]
    assert best_log(logs).path == "user_logs/std_log_process_0.txt"
    assert group_of("system_logs/lifecycler/execution-wrapper.log")[0] == "platform"
    assert group_of("azureml-logs/70_driver_log.txt")[0] == "driver"
    # an empty std_log says nothing: a log with content wins
    assert best_log([art("user_logs/std_log.txt", 0), art("logs/train.log", 500)]).path == "logs/train.log"


def test_pipeline_logs_list_stage_logs_first_never_the_orchestration_log(container: Container) -> None:
    svc = container.service
    cat = log_catalog(svc, "local:pipeline-nightly")
    assert cat["stages"] == ["nightly-train", "nightly-eval"]
    assert cat["files"][-1]["stage"] is None  # the pipeline's own "pipeline started" log comes last
    assert cat["recommended"] == cat["files"][0]["id"] and cat["files"][0]["stage"] is not None
    assert "pipeline started" not in svc.read_log("local:pipeline-nightly")
    one = log_catalog(svc, "local:pipeline-nightly", "nightly-eval")
    assert {f["stage"] for f in one["files"]} == {"nightly-eval"}
    # files of one stage are listed together, in the order the pipeline runs them
    stages = [f["stage"] for f in log_catalog(svc, "local:xf-pipe-finetune")["files"]]
    assert stages == sorted(stages, key=stages.index) and len(set(stages)) == 3


def test_read_one_log_as_a_tail_or_whole(container: Container) -> None:
    svc = container.service
    fid = log_catalog(svc, "local:pipeline-nightly")["recommended"]
    whole = read_log_file(svc, "local:pipeline-nightly", fid, None)
    assert not whole["partial"] and whole["read_bytes"] == whole["size_bytes"] and whole["stage"] == "nightly-train"
    tail = read_log_file(svc, "local:pipeline-nightly", fid, 100)
    assert tail["partial"] and tail["read_bytes"] == 100 and whole["text"].endswith(tail["text"])
    with pytest.raises(Exception, match="no log"):
        read_log_file(svc, "local:pipeline-nightly", "config.yaml", 100)


def test_log_routes(container: Container) -> None:
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    files = api.get("/api/v1/runs/local:xf-pipe-finetune/logs/files", params={"stage": "train_finetune"}).json()
    assert files["files"] and all(f["stage"] == "train_finetune" for f in files["files"])
    got = api.get("/api/v1/runs/local:xf-pipe-finetune/logs/file", params={"id": files["recommended"], "tail_bytes": 0})
    assert got.status_code == 200 and got.json()["text"] and not got.json()["partial"]
    assert api.get("/api/v1/runs/local:xf-pipe-finetune/logs/files", params={"stage": "nope"}).status_code == 400


def test_a_stage_loads_alone_when_the_pipeline_is_not_loaded(container: Container) -> None:
    svc = container.service
    asked: list[str] = []
    src = svc.source(svc.ref("local:xf-pipe-finetune"))
    real = src.get_metric_series

    def spy(run_id: str, *a: Any, **kw: Any) -> Any:
        asked.append(run_id)
        return real(run_id, *a, **kw)

    src.get_metric_series = spy  # type: ignore[method-assign]
    alone = {str(s.key): s for s in svc.stage_series("local:xf-pipe-finetune", "train_finetune")}
    assert asked == ["xf-pipe-finetune-train"]  # only that stage, not the pipeline and its other stages
    assert alone and all("stage=" not in k for k in alone)
    assert all(s.provenance.get("stage") == "train_finetune" for s in alone.values())
    # the same as the stage's view of the whole pipeline (which reuses the stage's fetch)
    merged = {str(s.key): s for s in stage_view(svc.series("local:xf-pipe-finetune"), "train_finetune")}
    assert set(merged) <= set(alone) and asked.count("xf-pipe-finetune-train") == 1
    assert all(merged[k].values == alone[k].values for k in merged)


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, status: int = 206, length: int | None = None) -> None:
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(length if length is not None else len(data))}

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def _gateway(monkeypatch: pytest.MonkeyPatch, body: bytes, honour_range: bool) -> tuple[Any, list[Any]]:
    seen: list[Any] = []

    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        seen.append(req)
        if getattr(req, "method", None) == "HEAD" or (hasattr(req, "get_method") and req.get_method() == "HEAD"):
            return _Resp(b"", 200, len(body))
        rng = req.headers.get("Range") if hasattr(req, "headers") else None
        if rng and honour_range:
            return _Resp(body[int(rng.split("=")[1].rstrip("-")) :], 206)
        return _Resp(body, 200)

    monkeypatch.setattr(rh, "urlopen", fake_urlopen)
    hist = SimpleNamespace(
        _base_url="https://region.api.azureml.ms",
        send_request=lambda req: SimpleNamespace(text=lambda: '"https://acct.blob.core.windows.net/c/f?sig=x"'),
    )
    return rh.RunHistoryGateway(
        SimpleNamespace(jobs=None), "sub", "rg", "ws", history=hist, container=SimpleNamespace()
    ), seen


@pytest.mark.parametrize("honour_range", [True, False])
def test_azure_log_tail_is_a_ranged_read(monkeypatch: pytest.MonkeyPatch, honour_range: bool) -> None:
    body = b"".join(f"line {i}\n".encode() for i in range(1000))
    gw, seen = _gateway(monkeypatch, body, honour_range)
    assert gw.download("exp", "job", "user_logs/std_log.txt", tail_bytes=50) == body[-50:]
    get = seen[-1]
    assert get.headers.get("Range") == f"bytes={len(body) - 50}-"  # never the whole file when the store has ranges
    assert gw.download("exp", "job", "user_logs/std_log.txt") == body  # no tail: the whole file, one request


def test_azure_stage_output_log_tail_reads_only_the_end() -> None:
    calls: list[dict[str, Any]] = []
    body = b"x" * 1000 + b"END"

    class Cont:
        def get_blob_client(self, name: str) -> Any:
            return SimpleNamespace(get_blob_properties=lambda: SimpleNamespace(size=len(body)))

        def download_blob(self, name: str, offset: int = 0) -> Any:
            calls.append({"name": name, "offset": offset})
            return SimpleNamespace(readall=lambda: body[offset:])

    gw = rh.RunHistoryGateway(
        SimpleNamespace(jobs=None), "sub", "rg", "ws", history=SimpleNamespace(), container=Cont()
    )
    assert gw.download("exp", "job", "stage_output/logs/train.log", tail_bytes=3) == b"END"
    assert calls == [{"name": "azureml/job/stage_output/logs/train.log", "offset": len(body) - 3}]
