"""The layering of docs/modular-design.md, enforced: a new import that breaks it fails here, with the reason.

Inner packages never import outer ones; analyses stay deterministic (no I/O, no adapters); the application uses
adapters only through ports — except the listed, known debt (design review D10), which may shrink but not grow.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "model_forensic"
ROOT = SRC.parents[2]

OUTER = {"container", "api", "cli", "notebook"}
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "shared": set(),
    "observability": {"shared"},
    "knowledge": {"shared", "domain"},
    "analysis": {"domain", "shared"},
    "interface": {"settings", "shared"},
    "settings": {"interface", "knowledge", "shared"},
    "application": {
        "(root)",
        "analysis",
        "domain",
        "shared",
        "knowledge",
        "projects",
        "interface",
        "settings",
        "observability",
        "infrastructure",
    },  # infrastructure: only the D10 list below
    "infrastructure": {"application", "domain", "shared", "settings"},  # application: the ports it implements
    "projects": {"application", "domain", "infrastructure", "shared"},
    "ai": {"application", "domain", "infrastructure", "shared", "analysis"},
    "reports": {"ai", "analysis", "application", "domain", "knowledge", "shared"},
    "calibration": {"analysis", "domain", "shared"},
    "(root)": {"notebook"},
}
# Known debt (design review D10): pure helpers that live in infrastructure but the application uses directly.
APPLICATION_INFRA_DEBT = {
    "application/inspect.py",
    "application/llm_connect.py",
    "application/plotting.py",
    "application/prefetch.py",
    "application/scheduler.py",
    "application/service.py",
}


def _imports() -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(SRC)
        src = (
            rel.parts[0][:-3]
            if len(rel.parts) == 1 and rel.name != "__init__.py"
            else (rel.parts[0] if len(rel.parts) > 1 else "(root)")
        )
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("model_forensic"):
                parts = n.module.split(".")
                tgt = parts[1] if len(parts) > 1 else "(root)"
                if tgt != src:
                    out[(src, tgt)].add(str(rel))
    return out


def test_layers_hold() -> None:
    bad = []
    for (src, tgt), files in sorted(_imports().items()):
        if src in OUTER:
            if tgt in ("cli",) or (src != "cli" and tgt == "api" and src != "api"):
                bad.append(f"{src} → {tgt} ({', '.join(sorted(files))}): an entry point must not be imported")
            continue
        if tgt not in ALLOWED.get(src, set()):
            bad.append(f"{src} → {tgt} ({', '.join(sorted(files))}): not allowed by docs/modular-design.md")
    assert not bad, "\n".join(bad)


def test_application_uses_adapters_only_through_ports_except_the_known_debt() -> None:
    files = _imports().get(("application", "infrastructure"), set())
    new = sorted(files - APPLICATION_INFRA_DEBT)
    assert not new, f"application imports an adapter directly in {new}: go through a port (application/ports.py)"
    gone = sorted(APPLICATION_INFRA_DEBT - files)
    assert not gone, f"debt paid off in {gone}: remove it from APPLICATION_INFRA_DEBT"


def test_no_private_cross_module_imports() -> None:
    bad = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("model_forensic"):
                own = p.relative_to(SRC.parent).with_suffix("").as_posix().replace("/", ".")
                if n.module == own:
                    continue
                for a in n.names:
                    if a.name.startswith("_") and not a.name.startswith("__"):
                        bad.append(f"{p.relative_to(SRC)} imports {n.module}.{a.name}")
    assert not bad, "private names are module-internal; make them public (analysis/series_ops.py):\n" + "\n".join(bad)


def test_module_catalogue_is_current() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "module_map.py"), "--check"], capture_output=True,
                       text=True, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
