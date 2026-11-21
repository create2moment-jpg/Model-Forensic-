"""In-process telemetry: counters and latency histograms, exported on /api/v1/diagnostics.

Intentionally dependency-free; an OpenTelemetry exporter can wrap ``Telemetry`` later.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class Telemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._latencies: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    @contextmanager
    def timed(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        ok = True
        try:
            yield
        except BaseException:
            ok = False
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            with self._lock:
                lat = self._latencies[name]
                lat.append(ms)
                if len(lat) > 2000:
                    del lat[:1000]
                self._counters[f"{name}.{'ok' if ok else 'error'}"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            lat = {}
            for k, v in self._latencies.items():
                s = sorted(v)
                lat[k] = (
                    {
                        "count": len(s),
                        "p50_ms": round(s[len(s) // 2], 2),
                        "p95_ms": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
                    }
                    if s
                    else {}
                )
            return {"counters": dict(self._counters), "latency": lat}


telemetry = Telemetry()
