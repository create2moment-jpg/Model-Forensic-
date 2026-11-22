"""Classified parser / schema messages — what a reader could not do, how bad it is, and what to do about it.

Every reader (evaluation JSONs, per-sequence parquets, metric names, configs, ``mf inspect``) reports problems as a
:class:`ParseMessage` instead of a free string, so the UI, ``mf inspect --json`` and the local-LLM remediation prompt
can sort, filter and act on them. Severities:

* ``info`` — worth knowing, nothing is wrong (e.g. no ruler in a config).
* ``recoverable_warning`` — something was skipped or mapped loosely; the rest is right (oversized file, unmapped
  metric name, file limit reached).
* ``unsupported_schema`` — a file the reader recognised by place or name has a shape it does not know: the
  ``expected`` and ``observed`` schemas say how it differs.
* ``parse_failure`` — the file could not be read at all (corrupt JSON, not a parquet).
* ``missing_data`` — the evidence a view needs is absent (no metric histories, no evaluation files). The view says
  so; it never shows a zero instead.
* ``critical`` — results would be wrong or nothing can be read (a reader dependency is missing).

Rule: an unknown format produces a visible message — never a crash and never a silent zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["info", "recoverable_warning", "unsupported_schema", "parse_failure", "missing_data", "critical"]
SEVERITIES: tuple[str, ...] = (
    "info", "recoverable_warning", "unsupported_schema", "parse_failure", "missing_data", "critical",
)  # fmt: skip
BLOCKING: frozenset[str] = frozenset({"unsupported_schema", "parse_failure", "critical"})


@dataclass(frozen=True)
class ParseMessage:
    severity: Severity
    code: str  # stable id, e.g. "eval.no_values", "parquet.schema", "naming.unmapped"
    message: str
    file: str | None = None
    expected: dict[str, Any] | None = None  # the schema the reader looks for
    observed: dict[str, Any] | None = None  # what the file has
    remediation: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING

    def text(self) -> str:
        return f"{self.file}: {self.message}" if self.file else self.message

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"blocking": self.blocking}


def counts(messages: list[ParseMessage]) -> dict[str, int]:
    out = dict.fromkeys(SEVERITIES, 0)
    for m in messages:
        out[m.severity] += 1
    return out


def shape_of(doc: Any, limit: int = 30) -> dict[str, Any]:
    """A small, value-free description of a parsed document (for ``observed``): types and top-level keys only."""
    if isinstance(doc, dict):
        keys = list(doc)[:limit]
        return {"type": "object", "keys": [str(k) for k in keys], "key_count": len(doc),
                "value_types": sorted({type(doc[k]).__name__ for k in keys})}  # fmt: skip
    if isinstance(doc, list):
        return {"type": "array", "length": len(doc), "item_types": sorted({type(x).__name__ for x in doc[:limit]})}
    return {"type": type(doc).__name__}
