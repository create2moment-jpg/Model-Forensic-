"""Generate docs/modules.md — the module catalogue of Model Forensic, from the code itself.

    python scripts/module_map.py          # rewrite docs/modules.md
    python scripts/module_map.py --check  # exit 1 if docs/modules.md is out of date (a test runs this)

Packages, their purpose, size, what they depend on and what depends on them (the real import graph), their main
modules (first docstring line), the external libraries and the web / instrumentation parts. The hand-written design
(layers, rules, decisions) is docs/modular-design.md; this file is the inventory it refers to.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src" / "model_forensic"
OUT = ROOT / "docs" / "modules.md"

PURPOSE = {
    "domain": "Pure types: runs, metric keys and series, findings, comparisons, configs, studies. No I/O.",
    "shared": "Cross-cutting utilities: errors, paths, retry, logging, identity, downsampling, web-build location.",
    "analysis": "Deterministic computation: analysis plugins, health questions, loss drift, internals, divergence, "
    "thresholds, series helpers. Reads domain types, never I/O or an LLM.",
    "application": "Use cases: the ForensicService (runs, series, configs, compare), run loading, tasks, prefetch, "
    "studies, ledger, expectations, dossier, work orders, calibration, checks. Defines the ports.",
    "infrastructure": "Adapters: Azure ML / local / demo connectors, SQLite persistence and caches, artifact store, "
    "search index, LLM providers (OpenAI-compatible, offline heuristic), notifications.",
    "knowledge": "Team knowledge: entries, store, inbox, documentation index, beliefs model.",
    "ai": "Grounded AI on top of the application: investigator, copilot, tools, prompt library, grounding.",
    "reports": "Markdown / HTML reports and the study book.",
    "api": "FastAPI app: the HTTP interface of every use case, auth, serving the web UI.",
    "cli": "The `mf` command line: every use case from a terminal (and for operator models).",
    "interface": "Site configuration readers: Azure workspaces, team policy (config/*.local.yaml).",
    "settings": "Settings model: paths, backends, LLM, storage (model-forensic.yaml + environment).",
    "projects": "Project profiles and the adapter contract (config/projects/*.local.yaml).",
    "observability": "Telemetry counters and timings.",
    "calibration": "Calibration of the comparison thresholds against replicate runs (P2.2).",
    "notebook": "Helpers for using Model Forensic from a Jupyter notebook.",
    "container": "Wiring: builds every service and adapter from the settings (the only place that does).",
    "(root)": "Package metadata (`__version__`).",
}

EXTERNAL = {
    "pydantic": "typed models and validation (domain, settings, contracts)",
    "numpy": "numerics of every analysis",
    "pyyaml": "configuration, knowledge, expectations, work orders",
    "httpx": "HTTP client (LLM endpoints, Azure REST)",
    "fastapi": "HTTP API",
    "uvicorn": "ASGI server for `mf serve`",
    "typer": "the `mf` command line",
    "azure-ai-ml": "Azure ML jobs and workspaces (read-only)",
    "azure-identity": "Azure credentials",
    "azure-storage-blob": "datastore files (read-only, SAS)",
    "pyarrow": "per-sequence evaluation parquets",
    "pandas": "notebook helpers",
    "PyJWT[crypto]": "OIDC token validation (team server)",
    "mlflow-skinny": "MLflow metric names / history (azure-mlflow extra)",
    "azureml-mlflow": "Azure ML MLflow tracking URI (azure-mlflow extra)",
    "pytest": "tests", "hypothesis": "property tests", "ruff": "lint + format", "mypy": "strict typing",
    "types-PyYAML": "typing stubs", "pandas-stubs": "typing stubs",
    "react": "UI", "react-dom": "UI", "react-router-dom": "routing", "@tanstack/react-query": "server state / caching",
    "lucide-react": "icons", "uplot": "fast time-series charts",
    "@playwright/test": "end-to-end journeys", "typescript": "types", "vite": "build / dev server",
    "@vitejs/plugin-react": "React for Vite", "@types/react": "typing", "@types/react-dom": "typing",
}  # fmt: skip


def _pkg(path: Path) -> str:
    rel = path.relative_to(SRC).parts
    return rel[0][:-3] if len(rel) == 1 and rel[0] != "__init__.py" else (rel[0] if len(rel) > 1 else "(root)")


def graph() -> tuple[dict[str, set[str]], dict[str, list[Path]]]:
    deps: dict[str, set[str]] = defaultdict(set)
    files: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        src = _pkg(p)
        files[src].append(p)
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("model_forensic"):
                parts = n.module.split(".")
                tgt = parts[1] if len(parts) > 1 else "(root)"
                if len(parts) > 1 and (SRC / f"{tgt}.py").exists():
                    pass
                if tgt != src:
                    deps[src].add(tgt)
    return deps, files


def _doc(p: Path) -> str:
    try:
        d = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8"))) or ""
    except SyntaxError:
        return ""
    return d.strip().splitlines()[0] if d.strip() else ""


def _deps_of(pyproject: Path) -> list[str]:
    text = pyproject.read_text(encoding="utf-8")
    names = re.findall(r'"([A-Za-z0-9_.\-\[\]]+?)(?:[<>=!~].*?)?"', text.split("[project.scripts]")[0])
    return [n for n in names if n in EXTERNAL]


def render() -> str:
    deps, files = graph()
    used_by: dict[str, set[str]] = defaultdict(set)
    for a, bs in deps.items():
        for b in bs:
            used_by[b].add(a)
    order = ["domain", "shared", "analysis", "knowledge", "application", "infrastructure", "ai", "reports",
             "projects", "interface", "settings", "observability", "calibration", "container", "api", "cli",
             "notebook", "(root)"]  # fmt: skip
    pk = [p for p in order if p in files] + sorted(p for p in files if p not in order)
    out = ["# Module catalogue (generated)", "",
           "Generated by `python scripts/module_map.py` from the code — do not edit; a test fails when it is out of "
           "date. The design (layers, rules, decisions) is in [modular-design.md](modular-design.md).", "",
           "## Backend packages", "",
           "| Package | Purpose | Files | Lines | Depends on | Used by |", "|---|---|---|---|---|---|"]  # fmt: skip
    for p in pk:
        lines = sum(f.read_text(encoding="utf-8").count("\n") for f in files[p])
        out.append(f"| `{p}` | {PURPOSE.get(p, '—')} | {len(files[p])} | {lines} | "
                   f"{', '.join(f'`{x}`' for x in sorted(deps.get(p, ()))) or '—'} | "
                   f"{', '.join(f'`{x}`' for x in sorted(used_by.get(p, ()))) or '—'} |")  # fmt: skip
    out += ["", "## Package dependencies", "", "```mermaid", "flowchart TB"]
    for a in pk:
        for b in sorted(deps.get(a, ())):
            if a in ("container", "api", "cli", "notebook", "(root)"):
                continue  # the outer layers use everything; drawn as one note below
            out.append(f"  {a.strip('()') or 'root'} --> {b.strip('()') or 'root'}")
    out += ["```", "", "`container`, `api`, `cli` and `notebook` are the outer layer: they may use every package above "
            "(and nothing uses them except the entry points).", "", "## Main modules", ""]  # fmt: skip
    for p in pk:
        if p in ("(root)",):
            continue
        big = sorted(files[p], key=lambda f: -f.read_text(encoding="utf-8").count("\n"))[:8]
        rows = [f"- `{f.relative_to(SRC.parent)}` ({f.read_text(encoding='utf-8').count(chr(10))} lines) — {_doc(f) or '—'}"
                for f in big if f.name != "__init__.py"]  # fmt: skip
        if rows:
            out += [f"### `{p}`", "", *rows, ""]
    out += ["## External dependencies", "", "| Package | Where | Used for |", "|---|---|---|"]
    for name in _deps_of(ROOT / "backend" / "pyproject.toml"):
        out.append(f"| `{name}` | backend | {EXTERNAL[name]} |")
    pkg = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    for kind in ("dependencies", "devDependencies"):
        for name, ver in sorted(pkg.get(kind, {}).items()):
            out.append(f"| `{name}` {ver} | web{' (dev)' if kind == 'devDependencies' else ''} | {EXTERNAL.get(name, '—')} |")
    out += ["| (none required) | instrument | torch / mlflow / numpy used when present in the training image |", ""]
    web = sorted((ROOT / "web" / "src").rglob("*.tsx"))
    out += ["## Web UI", "", f"{len(web)} components / pages under `web/src/`; main areas:", ""]
    for d in ("pages", "pages/run", "pages/compare", "components", "lib"):
        fs = sorted((ROOT / "web" / "src" / d).glob("*.ts*"))
        out.append(f"- `web/src/{d}/` ({len(fs)}): " + ", ".join(f.stem for f in fs))
    kit = sorted((ROOT / "instrument" / "src" / "model_forensic_instrument").glob("*.py"))
    out += ["", "## Instrumentation kit (`instrument/`, runs in the training job)", ""]
    out += [f"- `{f.name}` — {_doc(f) or '—'}" for f in kit if f.name != "__init__.py"]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("docs/modules.md is out of date: python scripts/module_map.py")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
