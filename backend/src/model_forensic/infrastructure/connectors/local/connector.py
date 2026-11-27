"""Local filesystem connector.

Reads runs exported in Model Forensic's normalized on-disk layout::

    <root>/<run_id>/run.json        # Run fields (without ``ref``)
    <root>/<run_id>/metrics.jsonl   # {"key": "val/NDS[range=0-30]", "step": 10, "value": 0.61, "ts": 1700000000}
    <root>/<run_id>/config.yaml     # any number of *.yaml / *.json under configs/ also accepted
    <root>/<run_id>/logs/*.txt
    <root>/<run_id>/outputs/**      # artifacts

It powers demo mode and offline investigations, and doubles as the export format of the
Azure ML connector's snapshots.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path

import yaml

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.domain.artifacts import Artifact, ArtifactKind
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.naming import NameMapper, NamingRules
from model_forensic.domain.resources import ResourceSeries, ResourceUsage
from model_forensic.domain.run import Run, RunRef
from model_forensic.shared import yamlsafe
from model_forensic.shared.errors import NotFoundError, ValidationError
from model_forensic.shared.paths import safe_join

_CHUNK = 1 << 20


def classify_artifact(path: str) -> ArtifactKind:
    """Kind from the path. Azure pipeline stage outputs are listed under ``stage_output/`` and follow the
    ``summaries/`` (configs, Lightning metrics.csv), ``evaluation/``, ``visualizations/``, ``checkpoints/`` layout."""
    p = path.lower()
    p = p.removeprefix("stage_output/")
    name = p.rsplit("/", 1)[-1]
    if p.startswith(("logs/", "user_logs/", "system_logs/", "azureml-logs/")) or p.endswith((".log", "std_log.txt")):
        return ArtifactKind.LOG
    if p.endswith((".pt", ".pth", ".ckpt", ".safetensors")) or "checkpoint" in p:
        return ArtifactKind.CHECKPOINT
    if name == "metrics.csv" or name in {"metrics.jsonl", "metrics.json"}:
        return ArtifactKind.METRICS
    if name == "composed_config.yaml" or p.endswith(("config.yaml", "config.json")) or "/configs/" in p:
        return ArtifactKind.CONFIG
    if p.startswith(("evaluation/", "visualizations/")) or name == "pipeline_info.json" or p.endswith(".parquet"):
        return ArtifactKind.EVALUATION
    if p.endswith(("conda.yaml", "requirements.txt", "environment.yml", "dockerfile")):
        return ArtifactKind.ENVIRONMENT
    if p.endswith((".yaml", ".yml")):
        return ArtifactKind.CONFIG
    if "metrics" in p:
        return ArtifactKind.METRICS
    if "eval" in p or "results" in p or p.startswith("summaries/"):
        return ArtifactKind.EVALUATION
    if "pred" in p:
        return ArtifactKind.PREDICTIONS
    return ArtifactKind.OTHER


class LocalRunSource:
    naming: NamingRules | None = None  # a project's metric names (project profile); None = built-in mapping only
    remote = False  # files on this machine: always read fresh (they may change), never kept in the metadata disk cache

    def __init__(self, name: str, root: str | Path) -> None:
        self.name = name
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        d = safe_join(self.root, run_id)
        if not (d / "run.json").is_file():
            raise NotFoundError(f"run {run_id!r} not found in {self.name}")
        return d

    def list_runs(self, query: ListRunsQuery) -> Page:
        if not self.root.is_dir():
            return Page(items=[])
        ids = sorted(p.name for p in self.root.iterdir() if (p / "run.json").is_file())
        start = int(query.cursor or 0)
        runs: list[Run] = []
        i = start
        while i < len(ids) and len(runs) < query.page_size:
            r = self.get_run(ids[i])
            i += 1
            if query.experiment and r.experiment != query.experiment:
                continue
            if query.updated_since and r.created_at and r.created_at < query.updated_since:
                continue
            runs.append(r)
        return Page(items=runs, next_cursor=str(i) if i < len(ids) else None)

    def get_run(self, run_id: str) -> Run:
        d = self._dir(run_id)
        data = json.loads((d / "run.json").read_text())
        data["ref"] = {"backend": self.name, "run_id": run_id}
        for f in ("parent",):
            if isinstance(data.get(f), str):
                data[f] = RunRef(backend=self.name, run_id=data[f]).model_dump()
        if "children" in data:
            data["children"] = [
                RunRef(backend=self.name, run_id=c).model_dump() if isinstance(c, str) else c for c in data["children"]
            ]
        return Run.model_validate(data)

    def resource_usage(self, run_id: str, start: datetime, end: datetime) -> ResourceUsage | None:
        """Machine metrics a local run folder carries in ``system_metrics.jsonl`` (``kind``, ``node``, ``device``,
        ``ts``, ``value`` per line) — None when it has none."""
        f = self._dir(run_id) / "system_metrics.jsonl"
        if not f.exists():
            return None
        acc: dict[tuple[str, str, str | None], tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
        for line in f.read_text().splitlines():
            try:
                p = json.loads(line)
                t, v = acc[(str(p["kind"]), str(p.get("node") or "node 0"), p.get("device"))]
                t.append(float(p["ts"]))
                v.append(float(p["value"]))
            except (ValueError, KeyError, TypeError):
                continue  # a broken line is skipped, the rest still shows
        return ResourceUsage(
            source="local file",
            series=[ResourceSeries(kind=k, node=n, device=d, t=t, v=v) for (k, n, d), (t, v) in acc.items()],
        )

    def _read_metrics(self, run_id: str) -> dict[MetricKey, list[tuple[float, float, float | None]]]:
        return self._read_metrics_raw(run_id)[0]

    def _read_metrics_raw(
        self, run_id: str
    ) -> tuple[dict[MetricKey, list[tuple[float, float, float | None]]], NameMapper]:
        path = self._dir(run_id) / "metrics.jsonl"
        out: dict[MetricKey, list[tuple[float, float, float | None]]] = defaultdict(list)
        names = NameMapper(self.naming)
        if not path.exists():
            return out, names
        with path.open() as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    key = names(str(rec["key"]))
                    val = float(rec["value"]) if rec["value"] is not None else float("nan")
                    out[key].append((float(rec.get("step", lineno)), val, rec.get("ts")))
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValidationError(f"{path}:{lineno}: malformed metric record") from exc
        return out, names

    def list_metric_keys(self, run_id: str) -> list[MetricKey]:
        return sorted(self._read_metrics(run_id), key=str)

    def get_metric_series(
        self,
        run_id: str,
        keys: Sequence[MetricKey] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[MetricSeries]:
        raw, names = self._read_metrics_raw(run_id)
        if on_progress:
            on_progress(len(raw), len(raw))  # one local file: read at once
        wanted = set(keys) if keys else None
        series = []
        for k, pts in raw.items():
            if wanted is not None and k not in wanted:
                continue
            pts.sort(key=lambda t: t[0])
            series.append(
                MetricSeries(
                    key=k,
                    steps=tuple(p[0] for p in pts),
                    values=tuple(p[1] for p in pts),
                    timestamps=tuple(float(p[2] or 0.0) for p in pts) if all(p[2] is not None for p in pts) else None,
                    source="metrics.jsonl",
                    provenance={"backend": self.name, "run_id": run_id, "raw_name": names.raw_of(k) or str(k)},
                )
            )
        return sorted(series, key=lambda s: str(s.key))

    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]:
        d = self._dir(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        snaps: list[ConfigurationSnapshot] = []
        candidates = [
            d / "config.yaml",
            d / "config.json",
            *sorted((d / "configs").glob("*.y*ml")),
            *sorted((d / "configs").glob("*.json")),
            *sorted((d / "stage_output").rglob("composed_config.yaml")),  # pipeline stage layout
        ]
        for f in candidates:
            if f.is_file():
                if f.stat().st_size > 2_000_000:
                    continue
                try:
                    text = f.read_text(errors="replace")
                    data = json.loads(text) if f.suffix == ".json" else yamlsafe.load(text)
                except (ValueError, yaml.YAMLError):  # malformed or unsafe config: skip it, keep the others
                    continue
                if isinstance(data, dict):
                    snaps.append(ConfigurationSnapshot(run=ref, source=f.relative_to(d).as_posix(), data=data))
        run = self.get_run(run_id)
        if run.params:
            snaps.append(ConfigurationSnapshot(run=ref, source="params", data=dict(run.params)))
        return snaps

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        d = self._dir(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        arts = []
        for f in sorted(d.rglob("*")):
            if f.is_file() and not f.is_symlink():
                rel = f.relative_to(d).as_posix()
                arts.append(Artifact(run=ref, path=rel, kind=classify_artifact(rel), size_bytes=f.stat().st_size))
        return arts

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        f = safe_join(self._dir(run_id), path)
        if not f.is_file():
            raise NotFoundError(f"artifact {path!r} not found")
        with f.open("rb") as fh:
            fh.seek(offset)
            while chunk := fh.read(_CHUNK):
                yield chunk

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str:
        d = self._dir(run_id) / "logs"
        logs = sorted(d.glob("*")) if d.is_dir() else []
        if not logs:
            raise NotFoundError("no logs for run")
        target = safe_join(d, name) if name else logs[0]
        if not target.is_file():
            raise NotFoundError(f"log {name!r} not found")
        size = target.stat().st_size
        with target.open("rb") as fh:
            if tail_bytes and size > tail_bytes:
                fh.seek(size - tail_bytes)
            return fh.read().decode("utf-8", errors="replace")
