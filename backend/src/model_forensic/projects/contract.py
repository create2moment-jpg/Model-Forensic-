"""The fixed adapter contract every run source (Azure ML, a local layout, a project's own adapter) must satisfy.

The checks are library code, not only tests: ``tests/contract`` runs them against every built-in adapter, and
``mf setup check`` / the setup agent run them against a real target after adapting its configuration. Each check
returns a result instead of raising, so a report lists everything that holds and everything that does not.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from model_forensic.application.ports import ListRunsQuery, RunSource
from model_forensic.domain.metrics import MetricKey
from model_forensic.projects.profile import CONTRACT_VERSION
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.paths import safe_join


@dataclass
class ContractResult:
    id: str
    title: str
    ok: bool
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(out: list[ContractResult], cid: str, title: str, fn: Callable[[], str], required: bool = True) -> None:
    try:
        out.append(ContractResult(cid, title, True, fn() or "", required))
    except AssertionError as exc:
        out.append(ContractResult(cid, title, False, str(exc) or "assertion failed", required))
    except Exception as exc:
        out.append(ContractResult(cid, title, False, f"{type(exc).__name__}: {str(exc)[:300]}", required))


def check_run_source(src: RunSource, max_runs: int = 3) -> list[ContractResult]:
    """Contract v1 for a run source. Reads at most ``max_runs`` runs; never writes anything."""
    out: list[ContractResult] = []
    runs: list[Any] = []

    def listing() -> str:
        page = src.list_runs(ListRunsQuery(page_size=max_runs))
        runs.extend(page.items[:max_runs])
        assert runs, "list_runs returned no runs — is this the right workspace / folder?"
        for r in runs:
            assert r.ref.backend == src.name, f"run {r.ref} does not carry this source's name {src.name!r}"
        return f"{len(runs)} run(s) listed"

    _check(out, "C1", "lists runs that carry the source's name", listing)
    if not runs:
        return out
    r0 = runs[0]

    def get() -> str:
        r = src.get_run(r0.ref.run_id)
        assert r.ref == r0.ref, "get_run returns a different run than list_runs"
        assert r.status is not None, "status missing"
        return f"{r.ref.run_id}: {r.status.value}"

    _check(out, "C2", "get_run is consistent with list_runs", get)

    def unknown() -> str:
        try:
            src.get_run("mf-contract-no-such-run-42")
        except NotFoundError:
            return "NotFoundError"
        raise AssertionError("an unknown run id must raise NotFoundError (not return or fail otherwise)")

    _check(out, "C3", "unknown run ids raise NotFoundError", unknown)

    def metrics() -> str:
        n = 0
        for r in runs:
            for s in src.get_metric_series(r.ref.run_id):
                assert isinstance(s.key, MetricKey), "series keys must be MetricKey"
                assert len(s.steps) == len(s.values), f"{s.key}: steps and values differ in length"
                n += 1
        assert n, "no metric series in the listed runs"
        return f"{n} series"

    _check(out, "C4", "metric series have canonical keys and aligned steps", metrics)

    def configs() -> str:
        n = sum(len(src.get_configs(r.ref.run_id)) for r in runs)
        return f"{n} config snapshot(s)" if n else "none (allowed — the config chapters will be empty)"

    _check(out, "C5", "configs are readable", configs, required=False)
    arts: list[Any] = []

    def artifacts() -> str:
        for r in runs:
            for a in src.list_artifacts(r.ref.run_id):
                safe_join(Path("/mf-contract-root"), a.path)  # must be relative, no traversal (raises otherwise)
                arts.append(a)
        return f"{len(arts)} file(s), all paths relative and safe"

    _check(out, "C6", "artifact paths are relative and safe", artifacts)

    def resume() -> str:
        a = next((x for x in arts if (x.size_bytes or 0) > 16), None)
        if a is None:
            return "no file larger than 16 bytes to test with"
        full = b"".join(src.open_artifact(a.run.run_id, a.path))
        tail = b"".join(src.open_artifact(a.run.run_id, a.path, offset=8))
        assert full[8:] == tail, "open_artifact(offset) must continue the same bytes (needed to resume downloads)"
        if a.size_bytes is not None:
            assert len(full) == a.size_bytes, f"{a.path}: listed size {a.size_bytes}, read {len(full)} bytes"
        return f"{a.path}: offset reads continue the file"

    _check(out, "C7", "downloads can resume from an offset", resume)

    def logs() -> str:
        for r in itertools.islice(runs, 2):
            try:
                text = src.read_log(r.ref.run_id, None, 2000)
                return f"{len(text)} characters"
            except NotFoundError:
                continue
        return "no logs (allowed)"

    _check(out, "C8", "logs are readable or absent (NotFoundError)", logs, required=False)
    return out


def summary(results: list[ContractResult]) -> dict[str, Any]:
    failed = [r for r in results if not r.ok and r.required]
    return {
        "contract_version": CONTRACT_VERSION,
        "ok": not failed,
        "passed": sum(r.ok for r in results),
        "failed_required": len(failed),
        "results": [r.to_dict() for r in results],
    }
