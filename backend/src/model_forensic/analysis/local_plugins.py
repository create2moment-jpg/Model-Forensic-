"""Your own analyses as plain Python files in a folder (default ``config/plugins/``, git-ignored by default).

A plugin file defines ``PLUGINS = (MyAnalysis(), ...)`` — objects with ``name``, ``version``, ``title``,
``description``, ``category``, ``requires``, ``limitations``, ``Params`` and ``run(ctx, params)``, exactly like the
built-ins (see ``analysis/plugins/*.py`` and ``config/plugins/example_analysis.py.example``). Files are reloaded
with ``POST /api/v1/analyses/reload`` or on restart; a broken file is reported, never fatal.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from model_forensic.analysis.registry import AnalysisRegistry

REQUIRED = ("name", "version", "title", "description", "category", "requires", "Params", "run")


def load_dir(folder: Path) -> tuple[list[Any], list[str]]:
    plugins: list[Any] = []
    errors: list[str] = []
    if not folder.is_dir():
        return plugins, errors
    for f in sorted(folder.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f"model_forensic_local_plugins.{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            for p in getattr(mod, "PLUGINS", ()):
                missing = [a for a in REQUIRED if not hasattr(p, a)]
                if missing:
                    errors.append(f"{f.name}: {getattr(p, 'name', p)!r} lacks {', '.join(missing)}")
                    continue
                plugins.append(p)
        except Exception as exc:
            errors.append(f"{f.name}: {type(exc).__name__}: {exc}")
    return plugins, errors


def install(
    registry: AnalysisRegistry, folders: list[Path], previous: set[str] | None = None
) -> tuple[set[str], list[str]]:
    """(Re)register the local plugins; returns their names and the load errors."""
    for name in previous or set():
        registry.unregister(name)
    names: set[str] = set()
    errors: list[str] = []
    for folder in folders:
        plugins, errs = load_dir(folder)
        errors += errs
        for p in plugins:
            try:
                registry.register(p)
                names.add(p.name)
            except ValueError as exc:
                errors.append(f"{p.name}: {exc} (built-in names cannot be replaced)")
    return names, errors
