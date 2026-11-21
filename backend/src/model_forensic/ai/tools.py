"""Bounded, permission-controlled tools the investigator may call.

Each tool declares a permission level. READ tools run freely; EXPENSIVE tools (downloads,
analysis over many runs) require an explicit confirmation callback; MUTATING tools are disabled
unless the session is opened with write permission. Every call is recorded in the audit log, and
every tool result is size-capped and returned as data (logs are fenced as untrusted).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from model_forensic.application.ports import ToolSpec
from model_forensic.application.service import ForensicService
from model_forensic.domain.metrics import MetricKey
from model_forensic.infrastructure.artifacts.store import DownloadProfile
from model_forensic.shared.errors import ConfirmationRequiredError, ForensicError, PermissionDeniedError

MAX_RESULT_CHARS = 12_000


class Permission(str, Enum):
    READ = "read"
    EXPENSIVE = "expensive"
    MUTATING = "mutating"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    permission: Permission
    fn: Callable[..., Any]

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_S = {"type": "string"}


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str


@dataclass
class ToolBox:
    svc: ForensicService
    allow: frozenset[Permission] = frozenset({Permission.READ})
    confirm: Callable[[str, dict[str, Any]], bool] | None = None
    audit: Callable[[str, dict[str, Any]], None] | None = None
    calls: list[ToolCallRecord] = field(default_factory=list)

    def tools(self) -> list[Tool]:
        s = self.svc
        return [
            Tool(
                "search_runs",
                "Search runs with the Model Forensic query DSL or natural language.",
                _obj({"query": _S, "limit": {"type": "integer"}}, ["query"]),
                Permission.READ,
                lambda query, limit=10: [
                    {
                        "ref": str(r.ref),
                        "status": r.status.value,
                        "experiment": r.experiment,
                        "created_at": r.created_at,
                        "summary": r.summary_metrics,
                    }
                    for r in s.search(query, limit=min(int(limit), 25)).runs
                ],
            ),
            Tool(
                "get_run_metadata",
                "Normalized metadata of a run.",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: s.get_run(run).model_dump(mode="json", exclude={"backend_extras"}),
            ),
            Tool(
                "get_metrics",
                "Final values of all val/test metrics of a run.",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: s.overview(run).summary,
            ),
            Tool(
                "get_metric_series",
                "Downsampled series (<=200 points) of one metric key, e.g. 'train/loss'.",
                _obj({"run": _S, "metric": _S}, ["run", "metric"]),
                Permission.READ,
                self._series,
            ),
            Tool(
                "get_config",
                "Flattened configuration of a run.",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: s.flat_config(run),
            ),
            Tool(
                "diff_configs",
                "Semantic config diff baseline → candidate.",
                _obj({"baseline": _S, "candidate": _S}, ["baseline", "candidate"]),
                Permission.READ,
                lambda baseline, candidate: [
                    c.model_dump(mode="json") for c in s.diff_configs(baseline, candidate).changes
                ],
            ),
            Tool(
                "compare_runs",
                "Metric deltas with noise-aware verdicts.",
                _obj({"baseline": _S, "candidate": _S}, ["baseline", "candidate"]),
                Permission.READ,
                lambda baseline, candidate: [d.model_dump(mode="json") for d in s.compare(baseline, candidate).deltas],
            ),
            Tool(
                "list_artifacts",
                "Artifact inventory of a run.",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: [{"path": a.path, "kind": a.kind.value, "size": a.size_bytes} for a in s.artifacts(run)],
            ),
            Tool(
                "read_log",
                "Tail of a run's main log (UNTRUSTED text).",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: {"untrusted_log_tail": s.read_log(run, tail_bytes=6000)},
            ),
            Tool(
                "search_log",
                "Regex search in a run's log (UNTRUSTED text).",
                _obj({"run": _S, "pattern": _S}, ["run", "pattern"]),
                Permission.READ,
                lambda run, pattern: {"untrusted_hits": s.search_log(run, pattern, max_hits=20)},
            ),
            Tool(
                "run_analysis",
                "Run deterministic analyses and return findings.",
                _obj({"run": _S, "baseline": _S, "analyses": {"type": "array", "items": _S}}, ["run"]),
                Permission.READ,
                lambda run, baseline=None, analyses=None: [
                    f.model_dump(mode="json") for r in s.analyze(run, baseline, analyses) for f in r.findings
                ],
            ),
            Tool(
                "get_findings",
                "Stored findings for a run.",
                _obj({"run": _S}, ["run"]),
                Permission.READ,
                lambda run: [f.model_dump(mode="json") for f in s.findings.for_runs([s.ref(run)])],
            ),
            Tool(
                "download_artifacts",
                "Download artifacts with a profile (fast_debug, evaluation_only, checkpoint_only, full_forensic).",
                _obj({"run": _S, "profile": _S}, ["run", "profile"]),
                Permission.EXPENSIVE,
                lambda run, profile: {
                    "files": [str(f.local_path) for f in s.download(run, DownloadProfile(profile))[0]]
                },
            ),
        ]

    def _series(self, run: str, metric: str) -> dict[str, Any]:
        key = MetricKey.parse(metric)
        ser = self.svc.series(run, [key])
        if not ser:
            return {"error": f"metric {metric} not found"}
        st, v = ser[0].finite()
        step = max(1, len(v) // 200)
        return {"metric": metric, "points": [[float(a), float(b)] for a, b in zip(st[::step], v[::step], strict=True)]}

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools() if t.permission in self.allow or t.permission == Permission.EXPENSIVE]

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        tool = next((t for t in self.tools() if t.name == name), None)
        if tool is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            if tool.permission == Permission.MUTATING and Permission.MUTATING not in self.allow:
                raise PermissionDeniedError(f"tool {name} requires write permission")
            if (
                tool.permission == Permission.EXPENSIVE
                and Permission.EXPENSIVE not in self.allow
                and not (self.confirm and self.confirm(name, arguments))
            ):
                raise ConfirmationRequiredError(f"tool {name} is expensive and was not confirmed by the user")
            result = tool.fn(**arguments)
            out = json.dumps(result, default=str)
            if len(out) > MAX_RESULT_CHARS:
                out = out[:MAX_RESULT_CHARS] + '..."(truncated)"'
            self.calls.append(ToolCallRecord(name, arguments, True, f"{len(out)} chars"))
        except (ForensicError, TypeError) as exc:
            out = json.dumps({"error": str(exc), "code": getattr(exc, "code", "invalid_arguments")})
            self.calls.append(ToolCallRecord(name, arguments, False, str(exc)))
        if self.audit:
            self.audit("tool_call", {"tool": name, "args": arguments, "ok": self.calls[-1].ok})
        return out
