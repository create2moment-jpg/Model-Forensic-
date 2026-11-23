"""The run's headline for the Overview: its combined metrics first, then one metric per class.

Configured in ``config/team.local.yaml`` → ``overview`` (``interface.team.OverviewPolicy``); classes in
``deprioritized_classes`` (and ``overview.exclude_classes``) are left out. Without configuration: keys matching
``*combined*``, and the evaluation metric with a ``class`` dimension that covers the most classes (F1 / score / AP
preferred). Pure: keys in, keys out — no project name in code.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence
from typing import Any

_PREFER = re.compile(r"f1|score|map|\bap\b|nds|auc", re.I)


def _split(key: str) -> tuple[str, dict[str, str]]:
    if "[" not in key or not key.endswith("]"):
        return key, {}
    base, inner = key[:-1].split("[", 1)
    return base, dict(p.split("=", 1) for p in inner.split(",") if "=" in p)


def _join(base: str, dims: dict[str, str]) -> str:
    return f"{base}[{','.join(f'{k}={v}' for k, v in sorted(dims.items()))}]" if dims else base


def _is_eval(key: str) -> bool:
    return key.startswith(("val/", "test/", "eval/")) and "loss" not in key.lower()


def headline(keys: Iterable[str], policy: Any, excluded: Sequence[str] = ()) -> dict[str, Any]:
    """``{"combined": [keys], "per_class": [{"class", "key"}], "per_class_metric": str, "keys": [...]}``."""
    ks = sorted(k for k in set(keys) if "stage=" not in k)  # the headline (last stage); a picked stage reads its own
    skip = {c.lower() for c in [*excluded, *policy.exclude_classes]}

    def is_combined(c: str) -> bool:
        return any(fnmatch.fnmatch(c.lower(), p.lower()) for p in policy.combined)

    combined: list[str] = []
    for pat in policy.combined:
        for k in ks:
            if k not in combined and _is_eval(k) and (fnmatch.fnmatch(k.lower(), pat.lower())):
                combined.append(k)
    if not combined:  # no combined metric logged: the plain evaluation metrics (NDS, mAP, …) stand in
        plain = [k for k in ks if _is_eval(k) and "[" not in k]
        combined = sorted(plain, key=lambda k: (not _PREFER.search(k), k))[:4]
    rows: list[dict[str, str]] = []
    family = ""
    tpl = policy.per_class_metric
    if "{class}" in tpl:  # the class is part of the name
        rx = re.compile("^" + re.escape(tpl).replace(re.escape("{class}"), r"(?P<cls>[A-Za-z0-9]+)") + "$")
        for k in ks:
            m = rx.match(k)
            if m and m.group("cls").lower() not in skip and k not in combined and not is_combined(m.group("cls")):
                rows.append({"class": m.group("cls"), "key": k})
        family = tpl
    else:
        fams: dict[str, list[tuple[str, str]]] = {}
        for k in ks:
            base, dims = _split(k)
            c = dims.pop(policy.class_dim, None)
            if c is None or not _is_eval(k) or len(dims) > 1:  # one class dimension (plus at most e.g. task)
                continue
            fams.setdefault(_join(base, dims), []).append((c, k))
        if tpl:
            cands = [f for f in fams if fnmatch.fnmatch(f, tpl)]
        else:
            cands = sorted(fams, key=lambda f: (-len(fams[f]), not _PREFER.search(f), f))
        if cands:
            family = cands[0]
            rows = [
                {"class": c, "key": k} for c, k in sorted(fams[family]) if c.lower() not in skip and not is_combined(c)
            ]
        if not tpl:  # the class can also be the metric's name: val/PassengerCar, val/PassengerCar_DetectionScore, …
            named, how = _named_classes(ks, policy.class_dim)
            named = [r for r in named if r["class"].lower() not in skip and not is_combined(r["class"])]
            if len(named) > len(rows):
                rows, family = named, how
    rows = rows[: policy.max_classes]
    return {"combined": combined, "per_class": rows, "per_class_metric": family,
            "keys": combined + [r["key"] for r in rows]}  # fmt: skip


def _named_classes(keys: Sequence[str], class_dim: str) -> tuple[list[dict[str, str]], str]:
    """Classes that are metric names: ``<split>/<Class>[…]`` where other metrics are named ``<Class>_<what>`` —
    one key per class (the one with the fewest dimensions, never one with a class-group dimension)."""
    names = {_split(k)[0].partition("/")[2] for k in keys if _is_eval(k)}
    best: dict[str, tuple[int, str]] = {}
    for k in keys:
        if not _is_eval(k):
            continue
        base, dims = _split(k)
        name = base.partition("/")[2]
        if not name or "_" in name or not name[:1].isupper() or class_dim in dims:
            continue
        if not any(o.startswith(name + "_") for o in names):
            continue
        if name not in best or len(dims) < best[name][0]:
            best[name] = (len(dims), k)
    rows = [{"class": c, "key": k} for c, (_, k) in sorted(best.items())]
    return rows, ("<split>/{class}" if rows else "")
