"""How a run used its machines over time — GPU and CPU utilisation, GPU and CPU memory, disk, network — per node (and
per GPU), as studio's job *Monitoring* tab shows it. Backend-neutral: Azure Monitor, the run's own logged system
metrics, or a local ``system_metrics.jsonl`` all become the same shape.
"""

from __future__ import annotations

import re

from model_forensic.domain.base import DomainModel

# the kinds the view and the checks know; values are percentages unless the name says MB
KINDS = ("gpu_util", "gpu_mem", "cpu_util", "cpu_mem", "disk_used", "net_in_mb", "net_out_mb")
LABEL = {
    "gpu_util": "GPU utilisation (%)", "gpu_mem": "GPU memory used (%)", "cpu_util": "CPU utilisation (%)",
    "cpu_mem": "CPU memory used (%)", "disk_used": "Disk used (%)", "net_in_mb": "Network in (MB)",
    "net_out_mb": "Network out (MB)",
}  # fmt: skip


class ResourceSeries(DomainModel):
    kind: str  # one of KINDS
    node: str = "node 0"
    device: str | None = None  # a GPU index for gpu_* kinds
    t: list[float] = []  # unix seconds
    v: list[float] = []


class ResourceUsage(DomainModel):
    source: str  # "azure monitor" | "logged by the run" | "local file"
    series: list[ResourceSeries] = []
    notes: list[str] = []


_LOGGED = (  # a logged metric name → its kind (MLflow system metrics, Lightning / custom names)
    (re.compile(r"gpu.*mem", re.I), "gpu_mem"),
    (re.compile(r"gpu.*(util|usage|percent)", re.I), "gpu_util"),
    (re.compile(r"(system|cpu).*mem(ory)?.*(percent|util|usage)", re.I), "cpu_mem"),
    (re.compile(r"cpu.*(util|usage|percent)", re.I), "cpu_util"),
    (re.compile(r"disk.*(percent|usage)", re.I), "disk_used"),
)
_GPU_INDEX = re.compile(r"gpu[_ ]?(\d+)", re.I)


def logged_kind(name: str) -> tuple[str, str | None] | None:
    """The resource kind of a metric the run logged itself (``system/gpu_0_utilization_percentage`` → gpu_util, GPU 0),
    or None for a training metric."""
    low = name.lower()
    if not any(w in low for w in ("gpu", "cpu", "memory", "disk")) or any(w in low for w in ("loss", "lr", "grad")):
        return None
    for pat, kind in _LOGGED:
        if pat.search(name):
            m = _GPU_INDEX.search(name)
            return kind, (m.group(1) if m and kind.startswith("gpu") else None)
    return None
