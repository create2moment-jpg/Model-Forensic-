"""Dataset provenance and compute details of a run (ported from the v1 tool, same rules).

**Dataset** — which dataset and split versions a run used, from two sources that differ in authority:

* the composed config's ``loomy_data_config`` block: the *requested* dataset (catalog basepath, name, the
  ``dataset_version`` map per usage, the local-dataset override, ``predict_usage``, ``dataset_split``,
  ``infer_latest_version_int``);
* the driver log: the *loaded* truth per split — ``loading dataset <name>:<version>,<shard> from index`` and
  ``Loaded Loomy dataset with name=…, version=…, usage=…, dataset_index_location_usage=<url>`` (the richer line wins).

**Compute** — what the run ran on: GPUs from the driver log (``Detected GPUs: [NvidiaGpuInfo(...)]`` preferred,
``GPU (k/n): name = …`` as fallback, memory normalised to GiB, per-process repeats collapsed, rows per log file because
multi-node runs log once per node; ``Detected GPUs: []`` is a CPU run, not missing evidence) and the requested
parallelism from the config (``trainer`` block, top-level ``data_config`` batch/worker values, the first
``run_environment.num_processes``; per-task overrides are noted, never merged).

Pure functions over parsed configs and matched log lines; every fact cites its source; nothing raises on foreign data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

# ------------------------------------------------------------------------------------------------ dataset
LOADING_RE = re.compile(r"loading dataset (?P<name>[^\s:]+):(?P<version>[^,\s]+),(?P<shard>\S+) from index")
LOADED_RE = re.compile(
    r"Loaded Loomy dataset with name=(?P<name>[^,]+), version=(?P<version>[^,]+), "
    r"usage=(?P<usage>[^,]+), dataset_index_location_usage=(?P<url>\S+)"
)
SPLIT_SUFFIXES = ("train", "training", "val", "validation", "test", "calibration", "quantization_check")
LOOMY_KEY = "loomy_data_config"

# ------------------------------------------------------------------------------------------------ compute
GPU_INFO_RE = re.compile(r"NvidiaGpuInfo\((?P<fields>[^)]*)\)")
GPU_FIELD_RE = re.compile(r"(?P<key>\w+)=(?P<quoted>'[^']*'|[^,)]+)")
GPU_LINE_RE = re.compile(
    r"GPU \((?P<index>\d+)/(?P<total>\d+)\): name = '(?P<name>[^']*)', "
    r"total memory = (?P<memory>[\d.]+) GiB, driver version = '(?P<driver>[^']*)'"
    r"(?:, VBIOS version = '(?P<vbios>[^']*)')?"
)
NO_GPU_RE = re.compile(r"Detected GPUs: \[\s*\]")
DATA_CONFIG_KEYS = (
    "batch_size_train",
    "batch_size_val",
    "batch_size_calibration",
    "num_workers_train",
    "num_workers_val",
    "num_workers_calibration",
    "pin_memory",
    "prefetch_factor",
    "persistent_workers",
)
TRAINER_KEYS = ("accelerator", "devices", "num_nodes", "strategy", "precision", "max_epochs", "max_steps")
OVERRIDE_KEY_RE = re.compile(r"^(batch_size_|num_workers_)")
BYTES_PER_GIB = 1024**3

#: Patterns for ``run_logs.scan_logs`` — only these lines are kept from the logs.
LOG_PATTERNS = (LOADING_RE, LOADED_RE, GPU_INFO_RE, GPU_LINE_RE, NO_GPU_RE)


@dataclass(frozen=True)
class LogLine:
    stage: str
    path: str
    line: str


@dataclass(frozen=True)
class SplitFact:
    split: str
    version: str
    dataset_name: str = ""
    index_url: str = ""
    source: str = ""


@dataclass
class DatasetProvenance:
    catalog_basepath: str = ""
    dataset_name: str = ""
    use_local_dataset: bool = False
    local_basepath: str = ""
    local_name: str = ""
    local_configured: list[SplitFact] = field(default_factory=list)
    predict_usage: str = ""
    dataset_split: str = ""
    infer_latest_version: str = ""
    configured: list[SplitFact] = field(default_factory=list)
    loaded: list[SplitFact] = field(default_factory=list)
    config_source: str = ""
    mismatches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_from_name(name: str) -> str:
    low = name.lower()
    return next((s for s in SPLIT_SUFFIXES if low.endswith(f"_{s}")), name)


def _split_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 and parts[-1] == "dataset_index" else ""


def _str(block: Mapping[str, Any], key: str) -> str:
    v = block.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else ""


def _scalar(block: Mapping[str, Any], key: str) -> str:
    v = block.get(key)
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _version_map(block: Mapping[str, Any], key: str, source: str) -> list[SplitFact]:
    raw = block.get(key)
    if not isinstance(raw, dict):
        return []
    return [
        SplitFact(split=str(u), version=str(v), source=source)
        for u, v in raw.items()
        if v is not None and not isinstance(v, (dict, list))
    ]


def loaded_splits(lines: Iterable[LogLine]) -> list[SplitFact]:
    """Loaded split facts; the richer 'Loaded Loomy dataset' line wins per split; first file (largest) wins."""
    by_file: dict[str, dict[str, SplitFact]] = {}
    for ln in lines:
        src = f"{ln.stage}/{ln.path}" if ln.stage else ln.path
        per = by_file.setdefault(src, {})
        m = LOADED_RE.search(ln.line)
        if m:
            name, url = m.group("name"), m.group("url")
            split = _split_from_name(name)
            if split == name:
                split = _split_from_url(url) or split
            per[split] = SplitFact(split, m.group("version"), name, url, src)
            continue
        m = LOADING_RE.search(ln.line)
        if m:
            name = m.group("name")
            per.setdefault(_split_from_name(name), SplitFact(_split_from_name(name), m.group("version"), name, "", src))
    out: list[SplitFact] = []
    seen: set[str] = set()
    for facts in by_file.values():
        for f in facts.values():
            if f.split not in seen:
                seen.add(f.split)
                out.append(f)
    return out


def dataset_provenance(configs: Sequence[tuple[str, Mapping[str, Any]]], lines: Sequence[LogLine]) -> DatasetProvenance:
    out = DatasetProvenance()
    block: Mapping[str, Any] | None = None
    for source, data in configs:
        b = data.get(LOOMY_KEY) if isinstance(data, Mapping) else None
        if isinstance(b, Mapping):
            block, out.config_source = b, source
            break
    if block is not None:
        src = out.config_source
        out.catalog_basepath = _str(block, "dataset_basepath")
        out.dataset_name = _str(block, "dataset_name")
        out.local_basepath = _str(block, "local_dataset_basepath")
        out.local_name = _str(block, "local_dataset_name")
        out.predict_usage = _str(block, "predict_usage")
        out.dataset_split = _str(block, "dataset_split")
        out.infer_latest_version = _scalar(block, "infer_latest_version_int")
        out.use_local_dataset = block.get("use_local_dataset") is True
        out.configured = _version_map(block, "dataset_version", src)
        out.local_configured = _version_map(block, "local_dataset_version", src)
        if not out.configured and block.get("dataset_version") is not None:
            out.notes.append("the config's dataset_version block is not a usage → version mapping.")
    elif configs:
        out.notes.append(f"no config has a {LOOMY_KEY} block — it may be a non-training stage config.")
    else:
        out.notes.append("no composed config found — download the Configs preset to see the requested dataset.")
    out.loaded = loaded_splits(lines)
    if not out.loaded:
        out.notes.append("no dataset-loading lines in the logs — download the Logs preset for per-split versions.")
    if out.use_local_dataset:
        out.notes.append("use_local_dataset is true: the local dataset and its versions are the ones used.")
    elif out.local_configured:
        out.notes.append("use_local_dataset is false: the local versions are configured but not used.")
    # Requested vs loaded: same split usage, different version — the comparison-breaking case.
    usage_alias = {"training": "train", "train": "train", "validation": "val", "val": "val", "test": "test"}
    req = {usage_alias.get(f.split.lower(), f.split.lower()): f for f in out.configured}
    for f in out.loaded:
        r = req.get(usage_alias.get(f.split.lower(), f.split.lower()))
        if r is not None and str(r.version) != str(f.version) and out.infer_latest_version != "true":
            out.mismatches.append(
                f"{f.split}: config requests version {r.version}, the log shows {f.dataset_name}:{f.version} loaded"
            )
    return out


# ------------------------------------------------------------------------------------------------ compute
@dataclass(frozen=True)
class GpuFact:
    name: str
    memory_gib: str
    memory_raw: str = ""
    driver_version: str = ""
    vbios_version: str = ""
    bus_id: str = ""
    pci_link_gen: str = ""
    source: str = ""


@dataclass(frozen=True)
class ParallelFact:
    key: str
    value: str
    source: str


@dataclass
class ComputeDetails:
    gpus: list[GpuFact] = field(default_factory=list)
    gpu_log_files: int = 0
    cpu_only: bool = False
    parallelism: list[ParallelFact] = field(default_factory=list)
    config_source: str = ""
    platform: dict[str, Any] = field(default_factory=dict)  # from the backend's job metadata
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gib(raw: str) -> str:
    try:
        return f"{int(raw) / BYTES_PER_GIB:.2f}"
    except ValueError:
        return ""


def _gpu_fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in GPU_FIELD_RE.finditer(text):
        v = m.group("quoted").strip()
        out[m.group("key")] = v[1:-1] if v.startswith("'") and v.endswith("'") else v
    return out


def gpus_in_file(lines: Iterable[str], source: str) -> list[GpuFact]:
    """GPUs one log file reports: NvidiaGpuInfo shape preferred, ``GPU (k/n)`` lines as fallback."""
    lines = list(lines)
    facts: dict[tuple[str, ...], GpuFact] = {}
    for line in lines:
        for m in GPU_INFO_RE.finditer(line):
            f = _gpu_fields(m.group("fields"))
            name = f.get("name", "")
            if not name:
                continue
            raw = f.get("memory_total", "")
            g = GpuFact(
                name,
                _gib(raw),
                raw,
                f.get("driver_version", ""),
                f.get("vbios_version", ""),
                f.get("bus_id", ""),
                f.get("max_pci_link_gen", ""),
                source,
            )
            facts.setdefault((name, g.bus_id, g.memory_gib), g)
    if facts:
        return list(facts.values())
    for line in lines:
        lm = GPU_LINE_RE.search(line)
        if lm:
            g = GpuFact(
                lm.group("name"),
                lm.group("memory"),
                f"{lm.group('memory')} GiB",
                lm.group("driver"),
                lm.group("vbios") or "",
                source=source,
            )
            facts.setdefault((g.name, f"gpu-{lm.group('index')}", g.memory_gib), g)
    return list(facts.values())


def _plain(block: Mapping[str, Any], keys: Sequence[str], source: str) -> list[ParallelFact]:
    return [
        ParallelFact(k, str(block[k]), source)
        for k in keys
        if k in block and block[k] is not None and not isinstance(block[k], (dict, list))
    ]


def _num_processes(node: Any) -> str | None:
    if isinstance(node, Mapping):
        env = node.get("run_environment")
        if isinstance(env, Mapping):
            v = env.get("num_processes")
            if v is not None and not isinstance(v, (dict, list)):
                return str(v)
        for v in node.values():
            found = _num_processes(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _num_processes(item)
            if found is not None:
                return found
    return None


def _has_task_overrides(doc: Mapping[str, Any]) -> bool:
    top = doc.get("data_config") if isinstance(doc.get("data_config"), Mapping) else {}

    def walk(node: Any) -> bool:
        if isinstance(node, Mapping):
            if node is not top and any(
                OVERRIDE_KEY_RE.match(str(k)) and not isinstance(v, (dict, list)) for k, v in node.items()
            ):
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(i) for i in node)
        return False

    return any(walk(v) for k, v in doc.items() if k != "data_config")


def compute_details(
    configs: Sequence[tuple[str, Mapping[str, Any]]],
    lines: Sequence[LogLine],
    platform: Mapping[str, Any] | None = None,
) -> ComputeDetails:
    out = ComputeDetails(platform={k: v for k, v in (platform or {}).items() if v not in (None, "", 0)})
    by_file: dict[str, list[str]] = {}
    for ln in lines:
        by_file.setdefault(f"{ln.stage}/{ln.path}" if ln.stage else ln.path, []).append(ln.line)
    saw_no_gpu = False
    for src, ls in by_file.items():
        g = gpus_in_file(ls, src)
        if g:
            out.gpu_log_files += 1
            out.gpus.extend(g)
        elif any(NO_GPU_RE.search(x) for x in ls):
            saw_no_gpu = True
    if not out.gpus:
        if saw_no_gpu:
            out.cpu_only = True
            out.notes.append("the driver log reports `Detected GPUs: []` — this run used no GPUs (CPU).")
        elif by_file:
            out.notes.append("no GPU lines in the logs — the stage may never have initialised CUDA.")
        else:
            out.notes.append("no driver-log GPU lines found — download the Logs preset to see the GPUs actually used.")
    doc: Mapping[str, Any] | None = None
    for source, data in configs:
        if isinstance(data, Mapping) and (
            isinstance(data.get("data_config"), Mapping) or isinstance(data.get("trainer"), Mapping)
        ):
            doc, out.config_source = data, source
            break
    if doc is not None:
        src = out.config_source
        if isinstance(doc.get("trainer"), Mapping):
            out.parallelism += _plain(doc["trainer"], TRAINER_KEYS, src)
        if isinstance(doc.get("data_config"), Mapping):
            out.parallelism += _plain(doc["data_config"], DATA_CONFIG_KEYS, src)
        npr = _num_processes(doc)
        if npr is not None:
            out.parallelism.append(ParallelFact("num_processes", npr, src))
        if out.parallelism and _has_task_overrides(doc):
            out.notes.append(
                "per-task data blocks override batch/worker values; the table shows the top-level data_config values."
            )
    elif configs:
        out.notes.append("no config with a trainer / data_config block.")
    else:
        out.notes.append("no composed config found — download the Configs preset to see batch sizes and workers.")
    return out
