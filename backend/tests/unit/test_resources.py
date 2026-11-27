"""The Resources view: machine metrics from Azure Monitor (needs Monitoring Reader), a local file, or what the run
logged itself — and the deterministic checks on them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.resources import findings, summarise
from model_forensic.container import Container
from model_forensic.domain.resources import ResourceSeries, ResourceUsage, logged_kind
from model_forensic.infrastructure.connectors.azureml.monitor import AzureMonitorGateway, interval_for
from model_forensic.shared.errors import PermissionDeniedError

T0 = datetime(2026, 11, 1, 8, tzinfo=UTC)


def flat(kind: str, level: float, node: str = "node 0", n: int = 100, device: str | None = None) -> ResourceSeries:
    return ResourceSeries(kind=kind, node=node, device=device, t=[float(i * 60) for i in range(n)], v=[level] * n)


def test_checks_name_the_bottleneck() -> None:
    dl = ResourceUsage(source="x", series=[flat("gpu_util", 35), flat("cpu_util", 97), flat("gpu_mem", 30)])
    titles = [f["title"] for f in findings(summarise(dl))]
    assert titles[0] == "GPUs busy only 35% of the time — the CPU is at 97%"
    assert any(t.startswith("GPU memory peaked at 30%") for t in titles)  # room for a larger batch
    healthy = ResourceUsage(source="x", series=[flat("gpu_util", 93), flat("cpu_util", 40), flat("gpu_mem", 80)])
    assert findings(summarise(healthy)) == []
    two = ResourceUsage(
        source="x", series=[flat("gpu_util", 92, "node 0"), flat("gpu_util", 55, "node 1"), flat("disk_used", 95)]
    )
    out = findings(summarise(two))
    assert any(f["title"].startswith("node 1 lags") for f in out)
    assert any(f["title"] == "Disk 95% full" and f["level"] == "problem" for f in out)


def test_start_up_is_not_the_pattern() -> None:
    # 5 % ramp at each end is ignored: a run idle only while starting is healthy
    v = [0.0] * 5 + [95.0] * 90 + [0.0] * 5
    s = ResourceSeries(kind="gpu_util", t=[float(i) for i in range(100)], v=v)
    assert summarise(ResourceUsage(source="x", series=[s]))["gpu_util"]["mean"] == 95.0


def test_logged_system_metric_names() -> None:
    assert logged_kind("system/gpu_0_utilization_percentage") == ("gpu_util", "0")
    assert logged_kind("system/gpu_1_memory_usage_percentage") == ("gpu_mem", "1")
    assert logged_kind("system/cpu_utilization_percentage") == ("cpu_util", None)
    assert logged_kind("system/system_memory_usage_percentage") == ("cpu_mem", None)
    assert logged_kind("train/loss") is None and logged_kind("val/NDS") is None


class FakeMonitor:
    """Azure Monitor's REST answers: the workspace's metric definitions, then the metrics per group."""

    def __init__(self, deny: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.deny = deny

    def __call__(self, url: str, token: str) -> Any:
        assert token == "t" and url.startswith("https://management.azure.com/subscriptions/sub/resourceGroups/rg/")
        if self.deny:
            raise PermissionDeniedError("Monitoring Reader needed")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.calls.append(q)
        if "metricDefinitions" in url:
            return {"value": [
                {"name": {"value": "GpuUtilizationPercentage"}, "dimensions": [{"value": "RunId"}, {"value": "InstanceId"}, {"value": "DeviceId"}]},
                {"name": {"value": "CpuUtilizationPercentage"}, "dimensions": [{"value": "RunId"}, {"value": "InstanceId"}]},
            ]}  # fmt: skip
        pts = lambda lv: [{"timeStamp": f"2026-11-01T08:0{i}:00Z", "average": lv} for i in range(3)]  # noqa: E731
        if "Gpu" in q["metricnames"]:
            return {"value": [{"name": {"value": "GpuUtilizationPercentage"}, "timeseries": [
                {"metadatavalues": [{"name": {"value": "InstanceId"}, "value": "n0"}, {"name": {"value": "DeviceId"}, "value": "0"}], "data": pts(90)},
                {"metadatavalues": [{"name": {"value": "InstanceId"}, "value": "n1"}, {"name": {"value": "DeviceId"}, "value": "0"}], "data": pts(40)},
            ]}]}  # fmt: skip
        return {"value": [{"name": {"value": "CpuUtilizationPercentage"}, "timeseries": [
            {"metadatavalues": [{"name": {"value": "InstanceId"}, "value": "n0"}], "data": pts(30)}]}]}  # fmt: skip


def gateway(fake: FakeMonitor) -> AzureMonitorGateway:
    cred = SimpleNamespace(get_token=lambda *a: SimpleNamespace(token="t"))
    return AzureMonitorGateway(cred, "sub", "rg", "ws", fetch=fake)


def test_azure_monitor_reads_only_what_the_workspace_offers_split_by_node_and_gpu() -> None:
    fake = FakeMonitor()
    u = gateway(fake).usage("job_1", T0, T0 + timedelta(hours=2))
    assert len(fake.calls) == 3  # definitions + GPU group + CPU group — memory / disk / network not offered: not asked
    gpu_q = next(c for c in fake.calls if "metricnames" in c and "Gpu" in c["metricnames"])
    assert gpu_q["$filter"] == "RunId eq 'job_1' and InstanceId eq '*' and DeviceId eq '*'"
    assert gpu_q["interval"] == "PT1M" and gpu_q["timespan"] == "2026-11-01T08:00:00Z/2026-11-01T10:00:00Z"
    gpu = {s.node: s for s in u.series if s.kind == "gpu_util"}
    assert gpu["n0"].v == [90.0, 90.0, 90.0] and gpu["n1"].device == "0"
    assert any(s.kind == "cpu_util" for s in u.series)
    assert any(f["title"].startswith("n1 lags") for f in findings(summarise(u)))
    gateway(fake).available()
    assert interval_for(30 * 3600) == "PT15M" and interval_for(20 * 86400) == "PT6H"


def test_missing_role_is_said_plainly() -> None:
    with pytest.raises(PermissionDeniedError):
        gateway(FakeMonitor(deny=True)).usage("job_1", T0, T0 + timedelta(hours=1))


def test_demo_runs_through_the_api(container: Container) -> None:
    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    r = c.get("/api/v1/runs/local:cand-radar-8f/resources").json()
    assert r["state"] == "ok" and r["source"] == "local file" and r["access"] is None
    assert r["findings"][0]["level"] == "problem" and "the CPU is at" in r["findings"][0]["title"]
    ok = c.get("/api/v1/runs/local:baseline-cl4f-s0/resources").json()
    assert ok["state"] == "ok" and not [f for f in ok["findings"] if f["level"] != "info"]
    none = c.get("/api/v1/runs/local:priya-bs32/resources").json()
    assert none["state"] == "none" and none["series"] == []
