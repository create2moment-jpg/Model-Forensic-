"""Where metrics go. All sinks take canonical names; MLflow-bound sinks encode them."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from model_forensic_instrument.naming import encode


class Sink:
    def log(self, metrics: Mapping[str, float], step: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def flush(self) -> None:
        pass


def _finite_or_nan(v: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return math.nan
    return f


class MemorySink(Sink):
    """Keeps everything in memory; handy for tests and notebooks."""

    def __init__(self) -> None:
        self.records: List[Tuple[str, int, float]] = []

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        self.records.extend((k, step, _finite_or_nan(v)) for k, v in metrics.items())

    def series(self, name: str) -> List[Tuple[int, float]]:
        return [(s, v) for k, s, v in self.records if k == name]

    def names(self) -> List[str]:
        return sorted({k for k, _, _ in self.records})


class MlflowSink(Sink):
    """Logs to the active MLflow run — in an Azure ML job this is the job itself."""

    MAX_BATCH = 900  # MLflow log_batch limit is 1000 metrics

    def __init__(self, mlflow_module: Optional[object] = None) -> None:
        if mlflow_module is None:
            import mlflow as mlflow_module  # type: ignore[no-redef]
        self._mlflow = mlflow_module

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        items = [(encode(k), _finite_or_nan(v)) for k, v in metrics.items()]
        for i in range(0, len(items), self.MAX_BATCH):
            self._mlflow.log_metrics(dict(items[i : i + self.MAX_BATCH]), step=step)  # type: ignore[attr-defined]


class JsonlSink(Sink):
    """Appends to ``metrics.jsonl`` in Model Forensic's local run layout (offline / non-Azure training)."""

    def __init__(self, path: os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        ts = time.time()
        with self._lock, self.path.open("a") as fh:
            for k, v in metrics.items():
                val = _finite_or_nan(v)
                fh.write(
                    json.dumps({"key": k, "step": int(step), "value": val if math.isfinite(val) else "NaN", "ts": ts})
                    + "\n"
                )


class CallableSink(Sink):
    """Forward to any function ``fn(metrics: dict, step: int)`` (TensorBoard, W&B, a logger...)."""

    def __init__(self, fn: Callable[[Dict[str, float], int], None], encode_names: bool = False) -> None:
        self.fn = fn
        self.encode_names = encode_names

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        self.fn({(encode(k) if self.encode_names else k): _finite_or_nan(v) for k, v in metrics.items()}, step)


class MultiSink(Sink):
    def __init__(self, sinks: Sequence[Sink]) -> None:
        self.sinks = list(sinks)

    def log(self, metrics: Mapping[str, float], step: int) -> None:
        for s in self.sinks:
            s.log(metrics, step)

    def flush(self) -> None:
        for s in self.sinks:
            s.flush()


def default_sink() -> Sink:
    """MLflow if importable (Azure ML jobs), otherwise ``./outputs/forensic/metrics.jsonl``."""
    try:
        import mlflow  # noqa: F401

        return MlflowSink()
    except ImportError:
        return JsonlSink(Path(os.environ.get("MF_INSTRUMENT_DIR", "outputs/forensic")) / "metrics.jsonl")
