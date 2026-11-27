"""Azure Monitor: a job's machine metrics — what studio's job *Monitoring* tab shows. Read-only GETs on the workspace's
``microsoft.insights/metrics`` (needs *Monitoring Reader* — or Reader — on the workspace; a 403 says so plainly).

Cost: one ``metricDefinitions`` read per workspace (cached), then one ``metrics`` read per group of metrics for the run
(GPU, CPU, memory, disk, network: at most four), split by node (``InstanceId``) and GPU (``DeviceId``), at an interval
that keeps each curve under ~1,500 points.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from model_forensic.domain.resources import ResourceSeries, ResourceUsage
from model_forensic.shared.errors import PermissionDeniedError

ARM = "https://management.azure.com"
API = "2018-01-01"
# kind → the workspace metric names that carry it (newest first); whichever the workspace offers is used
CANDIDATES: dict[str, tuple[str, ...]] = {
    "gpu_util": ("GpuUtilizationPercentage", "GpuUtilization"),
    "gpu_mem": ("GpuMemoryUtilizationPercentage", "GpuMemoryUtilization"),
    "cpu_util": ("CpuUtilizationPercentage", "CpuUtilization"),
    "cpu_mem": ("CpuMemoryUtilizationPercentage", "CpuMemoryUtilization"),
    "disk_used_mb": ("DiskUsedMegabytes",),
    "disk_avail_mb": ("DiskAvailMegabytes",),
    "net_in_mb": ("NetworkInputMegabytes",),
    "net_out_mb": ("NetworkOutputMegabytes",),
}
GROUPS = (
    ("gpu_util", "gpu_mem"),
    ("cpu_util", "cpu_mem"),
    ("disk_used_mb", "disk_avail_mb"),
    ("net_in_mb", "net_out_mb"),
)
ROLE_HINT = (
    "Azure Monitor refused the machine metrics (403): this needs the Monitoring Reader role (or Reader) on the "
    "workspace — ask the workspace owner to assign it. Until then only what the run logged itself is shown."
)

Fetch = Callable[[str, str], Any]


def _http(url: str, token: str) -> Any:
    if not url.startswith(ARM + "/"):  # only the management endpoint, never another scheme or host
        raise ValueError(f"not an Azure Resource Manager URL: {url[:60]}")
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})  # noqa: S310
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https host
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionDeniedError(ROLE_HINT) from exc
        raise


def interval_for(seconds: float) -> str:
    for limit, iv in ((6 * 3600, "PT1M"), (24 * 3600, "PT5M"), (3 * 86400, "PT15M"), (14 * 86400, "PT1H")):
        if seconds <= limit:
            return iv
    return "PT6H"


class AzureMonitorGateway:
    def __init__(
        self, credential: Any, subscription_id: str, resource_group: str, workspace: str, fetch: Fetch | None = None
    ) -> None:
        self._cred = credential
        self.resource = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.MachineLearningServices/workspaces/{workspace}"
        )
        self._fetch = fetch or _http
        self._names: dict[str, str] | None = None  # kind → the metric name this workspace offers

    def _token(self) -> str:
        return str(self._cred.get_token(f"{ARM}/.default").token)

    def available(self) -> dict[str, str]:
        """Which kinds this workspace's metrics offer (one read, then cached)."""
        if self._names is None:
            url = (
                f"{ARM}{self.resource}/providers/microsoft.insights/metricDefinitions?{urlencode({'api-version': API})}"
            )
            got = self._fetch(url, self._token())
            offered = {str(d.get("name", {}).get("value")): {str(x.get("value")) for x in d.get("dimensions") or []}
                       for d in got.get("value", [])}  # fmt: skip
            self._names = {}
            self._dims: dict[str, set[str]] = {}
            for kind, names in CANDIDATES.items():
                hit = next((n for n in names if n in offered), None)
                if hit:
                    self._names[kind] = hit
                    self._dims[hit] = offered[hit]
        return self._names

    def usage(self, run_id: str, start: datetime, end: datetime) -> ResourceUsage:
        names = self.available()
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        iv = interval_for((end - start).total_seconds())
        span = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        raw: dict[str, dict[tuple[str, str | None], tuple[list[float], list[float]]]] = {}
        notes: list[str] = []
        for group in GROUPS:
            metric_names = [names[k] for k in group if k in names]
            if not metric_names:
                continue
            dims = set().union(*(self._dims.get(n, set()) for n in metric_names))
            flt = f"RunId eq '{run_id}'" + (" and InstanceId eq '*'" if "InstanceId" in dims else "")
            flt += " and DeviceId eq '*'" if "DeviceId" in dims else ""
            q = urlencode({"api-version": API, "metricnames": ",".join(metric_names), "timespan": span,
                           "interval": iv, "aggregation": "Average", "$filter": flt}, quote_via=quote)  # fmt: skip
            got = self._fetch(f"{ARM}{self.resource}/providers/microsoft.insights/metrics?{q}", self._token())
            by_name = {v: k for k, v in names.items()}
            for m in got.get("value", []):
                kind = by_name.get(str(m.get("name", {}).get("value")))
                if kind is None:
                    continue
                for ts in m.get("timeseries", []):
                    meta = {str(x["name"]["value"]): str(x["value"]) for x in ts.get("metadatavalues", [])}
                    node = meta.get("InstanceId") or "node 0"
                    dev = meta.get("DeviceId")
                    pts = [
                        (p["timeStamp"], p.get("average")) for p in ts.get("data", []) if p.get("average") is not None
                    ]
                    if pts:
                        t = [datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp() for a, _ in pts]
                        raw.setdefault(kind, {})[(node, dev)] = (t, [float(b) for _, b in pts])
        missing = [k for k in ("gpu_util", "cpu_util") if k not in names]
        if missing:
            notes.append(f"the workspace offers no {' / '.join(missing)} metric")
        return ResourceUsage(source="azure monitor", series=_series(raw), notes=notes)


def _series(raw: dict[str, dict[tuple[str, str | None], tuple[list[float], list[float]]]]) -> list[ResourceSeries]:
    out: list[ResourceSeries] = []
    for kind in ("gpu_util", "gpu_mem", "cpu_util", "cpu_mem", "net_in_mb", "net_out_mb"):
        for (node, dev), (t, v) in sorted(raw.get(kind, {}).items(), key=lambda x: (x[0][0], x[0][1] or "")):
            out.append(ResourceSeries(kind=kind, node=node, device=dev, t=t, v=v))
    used, avail = raw.get("disk_used_mb", {}), raw.get("disk_avail_mb", {})
    for key, (t, u) in sorted(used.items(), key=lambda x: (x[0][0], x[0][1] or "")):
        a = dict(zip(*avail.get(key, ([], [])), strict=False))
        pct = [100 * x / (x + a[ts]) if ts in a and x + a[ts] > 0 else None for ts, x in zip(t, u, strict=True)]
        pts = [(ts, p) for ts, p in zip(t, pct, strict=True) if p is not None]
        if pts:
            out.append(
                ResourceSeries(
                    kind="disk_used", node=key[0], device=key[1], t=[p[0] for p in pts], v=[p[1] for p in pts]
                )
            )
    return out
