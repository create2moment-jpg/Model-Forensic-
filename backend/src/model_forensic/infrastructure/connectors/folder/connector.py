"""Local training / evaluation folders, read in place (read-only).

Point Model Forensic at the output folder of a local run — or at a folder holding several — and analyse it with the
same tooling as an Azure ML run. Nothing is copied into the folder or written to it, and nothing is kept in sync:
every read looks at the files as they are now (a run still training shows its latest values).

What is read (whatever is there):

* metrics — the instrumentation kit's ``metrics.jsonl`` (``outputs/forensic/``), Model Forensic's own
  ``metrics.jsonl``, PyTorch Lightning's ``metrics.csv`` (CSVLogger; the newest ``version_*``), TensorBoard event
  files (``tfevents.py``, no TensorFlow needed), evaluation result JSONs (``evalfiles.py``); names are mapped to
  canonical keys like every other source;
* configs — ``composed_config.yaml``, ``*config*.yaml`` / ``.json``, Lightning's ``hparams.yaml``;
* logs — ``*.log`` and ``logs/*``; evaluation files, checkpoints and the rest are listed as artifacts.

A folder is one run when it (or a folder up to four levels below it) holds a metrics file, a config or a log; a
folder without any of these at the top whose subfolders have them is a parent of runs — each subfolder one run.
Run ids are the folder name plus a short hash of its absolute path (stable, unique).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path

import yaml

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.naming import NameMapper, NamingRules
from model_forensic.domain.run import Run, RunKind, RunRef, RunStatus
from model_forensic.infrastructure.connectors.folder import evalfiles, tfevents
from model_forensic.infrastructure.connectors.local.connector import classify_artifact
from model_forensic.shared import yamlsafe
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.paths import safe_join

_CHUNK = 1 << 20
_DEPTH = 4
_MAX_FILES = 20_000
_RUNNING_S = 600  # a metrics file written in the last 10 minutes: the run is still going
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "wandb", ".ipynb_checkpoints"}
_SPLITS = {"train": "train", "training": "train", "val": "val", "valid": "val", "validation": "val", "eval": "val",
           "test": "test"}  # fmt: skip
_RUN_PARTS = {
    "lightning_logs",
    "outputs",
    "output",
    "logs",
    "checkpoints",
    "tensorboard",
    "tb_logs",
    "summaries",
    "evaluation",
    "eval",
    "configs",
    "metrics",
    "stage_output",
    "mlruns",
    "csv_logs",
}
_CONFIG = re.compile(r"(^|/)(composed_config\.ya?ml|hparams\.ya?ml|[^/]*config[^/]*\.(ya?ml|json))$", re.I)


def _files(root: Path, depth: int = _DEPTH) -> list[Path]:
    out: list[Path] = []
    stack = [(root, 0)]
    while stack and len(out) < _MAX_FILES:
        d, lv = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name in _SKIP_DIRS or e.is_symlink():
                continue
            if e.is_dir():
                if lv < depth:
                    stack.append((e, lv + 1))
            elif e.is_file():
                out.append(e)
    return out


def _signals(files: Sequence[Path], root: Path) -> dict[str, list[Path]]:
    sig: dict[str, list[Path]] = {"metrics": [], "events": [], "evals": [], "configs": [], "logs": []}
    for f in files:
        rel = f.relative_to(root).as_posix()
        n = f.name.lower()
        if n in ("metrics.jsonl", "metrics.csv"):
            sig["metrics"].append(f)
        elif n.startswith("events.out.tfevents"):
            sig["events"].append(f)  # TensorBoard
        elif evalfiles.is_eval_json(rel):
            sig["evals"].append(f)  # evaluation results
        elif _CONFIG.search(rel) and f.stat().st_size < 2_000_000:
            sig["configs"].append(f)
        elif n.endswith(".log") or rel.lower().startswith("logs/") or n == "std_log.txt":
            sig["logs"].append(f)
    return sig


def _newest_version(files: list[Path]) -> list[Path]:
    """Lightning keeps one ``version_N`` folder per launch: only the newest one's files (others: all of them)."""
    by: dict[str, list[Path]] = {}
    for f in files:
        v = next((p for p in reversed(f.parts[:-1]) if re.fullmatch(r"version_\d+", p)), "")
        by.setdefault(v, []).append(f)
    versioned = {k: fs for k, fs in by.items() if k}
    if not versioned:
        return files
    newest = max(versioned, key=lambda k: max(f.stat().st_mtime for f in versioned[k]))
    return [*by.get("", []), *versioned[newest]]


def _found(sig: dict[str, list[Path]]) -> str:
    """What a run folder holds, in a few words (the Local runs list shows it)."""
    parts = [*sorted({f.name for f in sig["metrics"]})]
    if sig["events"]:
        parts.append(f"TensorBoard ×{len(sig['events'])}")
    if sig["evals"]:
        parts.append(f"eval files ×{len(sig['evals'])}")
    if sig["configs"]:
        parts.append(f"configs ×{len(sig['configs'])}")
    if sig["logs"]:
        parts.append(f"logs ×{len(sig['logs'])}")
    return " · ".join(parts)


def run_id_of(path: Path) -> str:
    p = path.resolve()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", p.name).strip("-")[:60] or "run"
    return f"{slug}-{hashlib.sha256(str(p).encode()).hexdigest()[:6]}"


def discover(folder: Path) -> list[Path]:
    """The run folders under ``folder`` (itself, or its subfolders when it is a parent of runs)."""
    if not folder.is_dir():
        return []
    top = [f for f in folder.iterdir() if f.is_file()]
    if any(_signals(top, folder).values()):
        return [folder]
    subs = [d for d in sorted(folder.iterdir()) if d.is_dir() and d.name not in _SKIP_DIRS]
    runs = [d for d in subs if any(_signals(_files(d), d).values())]
    # a run's own output folders (lightning_logs/, outputs/, logs/, checkpoints/ …) or a single subfolder with
    # results: the folder itself is the run; several result subfolders: a folder of runs
    if len(runs) >= 2 and not any(d.name.lower() in _RUN_PARTS for d in runs):
        return runs
    return [folder] if runs or any(_signals(_files(folder), folder).values()) else []


class FolderRunSource:
    """Runs in local folders, read in place. ``folders``: run folders or parents of run folders."""

    naming: NamingRules | None = None
    remote = False  # local files: always read fresh

    def __init__(self, name: str, folders: Sequence[str | Path]) -> None:
        self.name = name
        self.folders = [Path(f).expanduser() for f in folders]

    # ── discovery ──────────────────────────────────────────────────────────────────────────────────────────────
    def _runs(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for f in self.folders:
            for d in discover(f):
                out[run_id_of(d)] = d.resolve()
        return out

    def _dir(self, run_id: str) -> Path:
        d = self._runs().get(run_id)
        if d is None:
            raise NotFoundError(f"run {run_id!r} not found in {self.name} (folder removed or moved?)")
        return d

    def list_runs(self, query: ListRunsQuery) -> Page:
        runs = [self.get_run(rid) for rid in sorted(self._runs())]
        if query.experiment:
            runs = [r for r in runs if r.experiment == query.experiment]
        return Page(items=runs)

    def get_run(self, run_id: str) -> Run:
        d = self._dir(run_id)
        files = _files(d)
        sig = _signals(files, d)
        mtimes = [f.stat().st_mtime for f in files] or [d.stat().st_mtime]
        newest_metrics = max((f.stat().st_mtime for f in [*sig["metrics"], *sig["events"]]), default=0.0)
        running = newest_metrics and (datetime.now(timezone.utc).timestamp() - newest_metrics) < _RUNNING_S
        tail = ""
        if sig["logs"]:
            try:
                last = max(sig["logs"], key=lambda f: f.stat().st_mtime)
                with last.open("rb") as fh:
                    fh.seek(max(0, last.stat().st_size - 4000))
                    tail = fh.read().decode("utf-8", errors="replace")
            except OSError:
                tail = ""
        failed = bool(re.search(r"Traceback \(most recent call last\)|CUDA out of memory|Error: ", tail))
        keys = self.list_metric_keys(run_id)
        has_train = any(k.split.value == "train" for k in keys)
        status = RunStatus.RUNNING if running else RunStatus.FAILED if failed else RunStatus.COMPLETED
        return Run(
            ref=RunRef(backend=self.name, run_id=run_id),
            name=d.name,
            display_name=d.name,
            experiment=d.parent.name,
            kind=RunKind.TRAINING if has_train else RunKind.EVALUATION if keys else RunKind.OTHER,
            status=status,
            created_at=datetime.fromtimestamp(min(mtimes), tz=timezone.utc),
            started_at=datetime.fromtimestamp(min(mtimes), tz=timezone.utc),
            ended_at=None if running else datetime.fromtimestamp(max(mtimes), tz=timezone.utc),
            description=f"local folder {d}",
            tags={"source": "local-folder"},
            backend_extras={"path": str(d), "found": _found(sig)},
        )

    def version(self, run_id: str) -> str:
        """A fingerprint of the run's metrics, configs and logs (times and sizes): it changes when the run writes."""
        d = self._dir(run_id)
        sig = _signals(_files(d), d)
        parts = sorted(f"{f}:{f.stat().st_mtime_ns}:{f.stat().st_size}" for fs in sig.values() for f in fs)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    # ── metrics ────────────────────────────────────────────────────────────────────────────────────────────────
    def _metric_files(self, d: Path) -> tuple[list[Path], Path | None, list[Path], list[Path]]:
        sig = _signals(_files(d), d)
        jsonl = [f for f in sig["metrics"] if f.name.lower() == "metrics.jsonl"]
        csvs = [f for f in sig["metrics"] if f.name.lower() == "metrics.csv"]
        newest_csv = max(csvs, key=lambda f: f.stat().st_mtime) if csvs else None  # Lightning: the latest version
        return jsonl, newest_csv, _newest_version(sig["events"]), sig["evals"]

    def _read(self, run_id: str) -> tuple[dict[MetricKey, list[tuple[float, float, float | None]]], NameMapper, str]:
        d = self._dir(run_id)
        names = NameMapper(self.naming)
        out: dict[MetricKey, list[tuple[float, float, float | None]]] = defaultdict(list)
        jsonl, mcsv, events, evals = self._metric_files(d)
        sources: list[str] = []
        for f in jsonl:
            sources.append(f.relative_to(d).as_posix())
            with f.open(errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    try:
                        rec = json.loads(line)
                        v = rec.get("value")
                        val = float(v) if v is not None else math.nan
                        out[names(str(rec["key"]))].append((float(rec.get("step", lineno)), val, rec.get("ts")))
                    except (KeyError, ValueError, TypeError):
                        continue  # a malformed line of a file still being written
        if mcsv is not None:
            sources.append(mcsv.relative_to(d).as_posix())
            with mcsv.open(newline="", errors="replace") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    step = row.get("step") or row.get("epoch") or str(i)
                    try:
                        st = float(step)
                    except ValueError:
                        st = float(i)
                    for col, raw in row.items():
                        if col in ("step", "epoch") or col is None or raw in ("", None):
                            continue
                        try:
                            out[names(col)].append((st, float(raw), None))
                        except (ValueError, TypeError):
                            continue
        if events:  # TensorBoard: scalars; a train/ or val/ writer folder gives the split to unprefixed tags
            sources.append(f"{len(events)} TensorBoard event file(s)")
            for f in events:
                split = next((x for x in reversed(f.relative_to(d).parts[:-1]) if x.lower() in _SPLITS), None)
                for tag, st, val, wall in tfevents.read_scalars(f):
                    name = tag if ("/" in tag or not split) else f"{_SPLITS[split.lower()]}/{tag}"
                    out[names(name)].append((st, val, wall or None))
        if evals:  # evaluation result files: one value each, or a curve over checkpoints
            groups = {evalfiles.group_and_step(f.relative_to(d).as_posix())[0] for f in evals}
            sources.append(f"{len(evals)} evaluation file(s)")
            for f in sorted(evals):
                rel = f.relative_to(d).as_posix()
                group, step = evalfiles.group_and_step(rel)
                for name, dims, val in evalfiles.read(f, rel):
                    if len(groups) > 1:
                        dims = {**dims, "file": group}
                    inner = ",".join(f"{k}={v}" for k, v in sorted(dims.items()) if v)
                    out[names(f"{name}[{inner}]" if inner else name)].append(
                        (step if step is not None else 0.0, val, None)
                    )
        return out, names, ", ".join(sources) or "no metrics file"

    def list_metric_keys(self, run_id: str) -> list[MetricKey]:
        return sorted(self._read(run_id)[0], key=str)

    def get_metric_series(
        self,
        run_id: str,
        keys: Sequence[MetricKey] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[MetricSeries]:
        raw, names, source = self._read(run_id)
        if on_progress:
            on_progress(len(raw), len(raw))
        wanted = set(keys) if keys else None
        series = []
        for k, pts in raw.items():
            if wanted is not None and k not in wanted:
                continue
            pts.sort(key=lambda t: t[0])
            series.append(MetricSeries(
                key=k, steps=tuple(p[0] for p in pts), values=tuple(p[1] for p in pts),
                timestamps=tuple(float(p[2] or 0.0) for p in pts) if all(p[2] is not None for p in pts) else None,
                source=source, provenance={"backend": self.name, "run_id": run_id, "raw_name": names.raw_of(k) or str(k)},
            ))  # fmt: skip
        return sorted(series, key=lambda s: str(s.key))

    # ── configs, files, logs ───────────────────────────────────────────────────────────────────────────────────
    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]:
        d = self._dir(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        snaps: list[ConfigurationSnapshot] = []
        for f in _signals(_files(d), d)["configs"]:
            try:
                text = f.read_text(errors="replace")
                data = json.loads(text) if f.suffix == ".json" else yamlsafe.load(text)
            except (ValueError, yaml.YAMLError, OSError):
                continue  # malformed or unsafe: skip it, keep the others
            if isinstance(data, dict):
                snaps.append(ConfigurationSnapshot(run=ref, source=f.relative_to(d).as_posix(), data=data))
        return snaps

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        d = self._dir(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        return [
            Artifact(
                run=ref,
                path=(rel := f.relative_to(d).as_posix()),
                kind=classify_artifact(rel),
                size_bytes=f.stat().st_size,
            )
            for f in _files(d, depth=8)
        ]

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        f = safe_join(self._dir(run_id), path)
        if not f.is_file():
            raise NotFoundError(f"artifact {path!r} not found")
        with f.open("rb") as fh:
            fh.seek(offset)
            while chunk := fh.read(_CHUNK):
                yield chunk

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str:
        d = self._dir(run_id)
        logs = sorted(_signals(_files(d), d)["logs"], key=lambda f: f.stat().st_mtime, reverse=True)
        if not logs:
            raise NotFoundError("no logs in this folder (*.log or logs/)")
        target = safe_join(d, name) if name else logs[0]
        if not target.is_file():
            raise NotFoundError(f"log {name!r} not found")
        size = target.stat().st_size
        with target.open("rb") as fh:
            if tail_bytes and size > tail_bytes:
                fh.seek(size - tail_bytes)
            return fh.read().decode("utf-8", errors="replace")


def preview(folder: str | Path) -> dict[str, object]:
    """What Model Forensic would read in ``folder`` — before adding it."""
    p = Path(folder).expanduser()
    if not p.is_dir():
        return {"ok": False, "path": str(p), "problem": "not a folder (or not readable)", "runs": []}
    runs = []
    for d in discover(p)[:200]:
        sig = _signals(_files(d), d)
        runs.append({"path": str(d), "name": d.name, "run_id": run_id_of(d),
                     "metrics": [f.relative_to(d).as_posix() for f in [*sig["metrics"], *sig["events"]]][:5],
                     "evals": [f.relative_to(d).as_posix() for f in sig["evals"]][:5],
                     "tensorboard": len(sig["events"]),
                     "configs": [f.relative_to(d).as_posix() for f in sig["configs"]][:5],
                     "logs": [f.relative_to(d).as_posix() for f in sig["logs"]][:5]})  # fmt: skip
    return {"ok": bool(runs), "path": str(p.resolve()), "runs": runs,
            "problem": "" if runs else "no metrics file, config or log found (up to four levels deep)"}  # fmt: skip
