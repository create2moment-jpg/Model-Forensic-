"""Evaluation result files of a local run (JSON) → evaluation metrics.

Which files: ``*.json`` under an ``eval`` / ``evaluation`` / ``results`` / ``test_results`` folder, or named like
``eval*.json``, ``*metric*.json``, ``*result*.json``, ``*score*.json``, ``*summary*.json`` — never a config. Every
number in the file becomes a metric: the leaf's name, the path to it as ``scope=a.b`` (``{"car": {"AP": .6}}`` →
``val/AP[scope=car]``), the split ``test`` when the path says so (else ``val``), and ``file=<name>`` when the run
has several kinds of result file. A file numbered by checkpoint (``eval_epoch_12.json``, ``results_step5000.json``)
is one point of a curve over checkpoints (step = that number); otherwise a single value.
Untrusted input: size-capped, depth- and count-limited, lists skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_BYTES = 5_000_000
MAX_VALUES = 2000
_DIRS = {"eval", "evaluation", "evaluations", "results", "result", "test_results", "eval_results", "metrics"}
_NAME = re.compile(r"(eval|metric|result|score|summary)", re.I)
_NOT = re.compile(r"(config|hparams|tsconfig|package|vocab|tokenizer|label_map|classes|meta)", re.I)
_NUM = re.compile(r"(?:epoch|step|iter|ckpt|checkpoint|e|s)?[_\-=]?(\d+)", re.I)


def is_eval_json(rel: str) -> bool:
    p = rel.lower()
    if not p.endswith(".json") or _NOT.search(p.rsplit("/", 1)[-1]):
        return False
    parts = p.split("/")
    return any(x in _DIRS for x in parts[:-1]) or bool(_NAME.search(parts[-1]))


def group_and_step(rel: str) -> tuple[str, float | None]:
    """``eval_epoch_12.json`` → (``eval_epoch``, 12): files differing only in that number form one curve."""
    stem = rel.rsplit("/", 1)[-1][: -len(".json")]
    nums = list(_NUM.finditer(stem))
    if not nums:
        return stem, None
    m = nums[-1]
    group = (stem[: m.start(1)] + stem[m.end(1) :]).strip("_-=.") or "eval"
    return re.sub(r"[_\-=]+$", "", group), float(m.group(1))


def _key_part(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(x)).strip("_")[:60] or "x"


def read(path: Path, rel: str) -> list[tuple[str, dict[str, str], float]]:
    """``(name, dims, value)`` for every number in the file (dims: scope, and the split goes into the name)."""
    if path.stat().st_size > MAX_BYTES:
        return []
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except ValueError:
        return []
    split = "test" if "test" in rel.lower() else "val"
    out: list[tuple[str, dict[str, str], float]] = []

    def walk(node: object, trail: list[str], depth: int) -> None:
        if len(out) >= MAX_VALUES or depth > 5:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, [*trail, _key_part(k)], depth + 1)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)) and trail:
            dims = {"scope": ".".join(trail[:-1])} if len(trail) > 1 else {}
            out.append((f"{split}/{trail[-1]}", dims, float(node)))

    walk(doc, [], 0)
    return out
