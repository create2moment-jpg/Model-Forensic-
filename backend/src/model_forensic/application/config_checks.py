"""Configuration checks — each with a stable id, severity, current vs expected state, a remediation, the local-LLM
prompt that repairs it, and the command that validates the repair (check → prompt → validation).

The checks only read (configuration files, the index, task timings, prefetch tasks, the LLM status); they never
change anything and never call Azure beyond what the application already did. The prompts live in
``setup-agent/prompts/`` and are written for a local LLM working inside the allowed adaptation area.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from model_forensic.application.ports import SearchFilters
from model_forensic.application.setup_check import _git_ignored
from model_forensic.shared.paths import mf_home

Severity = Literal["critical", "warning", "info"]
Status = Literal["pass", "fail", "unknown", "skip"]
PROMPTS = "setup-agent/prompts"


@dataclass
class Check:
    id: str
    area: str
    title: str
    severity: Severity
    status: Status
    current: str
    expected: str
    remediation: str
    prompt: str  # setup-agent/prompts/<file>.md
    validation: str  # the command / endpoint that confirms the fix

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"prompt_path": f"{PROMPTS}/{self.prompt}"}


def run(container: Any) -> list[Check]:
    from model_forensic.application.storage import validate_path

    s = container.settings
    svc = container.service
    home = mf_home()
    out: list[Check] = []

    def add(*a: Any) -> None:
        out.append(Check(*a))

    # ---- interface / workspaces
    azure = home / "config" / "azure.local.yaml"
    placeholders = [n for n in s.notices if n.startswith("Azure interface")]
    add("CFG-AZ-1", "interface", "Azure interface file filled in", "critical" if placeholders else "info",
        "fail" if placeholders else ("pass" if azure.is_file() else "skip"),
        placeholders[0] if placeholders else (str(azure) if azure.is_file() else "no config/azure.local.yaml (local / demo only)"),
        "config/azure.local.yaml with every <placeholder> replaced", "fill in the placeholders (mf init writes the template)",
        "interface_diagnostics.md", "mf doctor --live --json --bundle support.json")  # fmt: skip
    errs = dict(container.backend_errors)
    add("CFG-WS-1", "interface", "Every configured workspace connects", "critical", "fail" if errs else "pass",
        "; ".join(f"{k}: {v[:120]}" for k, v in errs.items()) or f"{len(svc.sources)} workspace(s) connected",
        "no connection error", "read the diagnosis in support.json (login, tenant, proxy / CA, role)",
        "interface_diagnostics.md", "mf doctor --live --json")  # fmt: skip
    # ---- projects
    perr = [n for n in s.notices if n.startswith("Project")]
    add("CFG-PROJ-1", "projects", "Project profiles load", "warning", "fail" if perr else "pass",
        "; ".join(perr)[:300] or f"{len(svc.projects)} profile(s)", "every config/projects/*.yaml valid (contract v1)",
        "fix the profile named in the notice", "configuration_check_and_repair.md", "mf projects check <id>")  # fmt: skip
    # ---- metric columns and names
    try:
        res = svc.index.search(SearchFilters(), 500, 0)
        runs = list(res.runs)
    except Exception:
        runs = []
    if runs:
        from model_forensic.application.metric_columns import columns

        mc = columns(runs, list(svc.projects), {n: bool(getattr(x, "remote", True)) for n, x in svc.sources.items()})
        frac = mc["runs_with_values"] / max(1, mc["runs"])
        add("CFG-MC-1", "metrics", "Runs table metric columns have values", "warning" if frac < 0.5 else "info",
            "pass" if frac >= 0.5 else "fail", f"{mc['runs_with_values']}/{mc['runs']} indexed runs have metric values",
            "most finished runs have values", "Runs → Fill metric columns (loads histories as background tasks)",
            "metric_columns_diagnostics.md", "POST /api/v1/runs/metric-columns · tests/unit/test_metric_columns.py")  # fmt: skip
    else:
        add("CFG-MC-1", "metrics", "Runs table metric columns have values", "info", "skip", "no runs indexed yet",
            "indexed runs", "sync the workspaces", "metric_columns_diagnostics.md", "mf sync")  # fmt: skip
    # ---- task configuration
    local = home / "config" / "tasks.local.yaml"
    status: Status = "pass"
    cur = "shipped defaults only"
    if local.is_file():
        try:
            from model_forensic.application.tasks import load_specs

            load_specs(Path(__file__).parents[1] / "defaults" / "tasks.yaml", local)
            unknown = [
                k for k in (yaml.safe_load(local.read_text()) or {}).get("tasks", {}) if k not in container.tasks.specs
            ]
            status, cur = ("fail", f"unknown task kind(s): {', '.join(unknown)}") if unknown else ("pass", str(local))
        except Exception as exc:
            status, cur = "fail", f"{type(exc).__name__}: {str(exc)[:200]}"
    add("CFG-TASK-1", "performance", "Local task-cost overrides are valid", "warning", status, cur,
        "config/tasks.local.yaml absent, or only known kinds with valid cost classes", "fix or remove the entry",
        "run_light_heavy_audit.md", "GET /api/v1/test-space (production table)")  # fmt: skip
    disagree = []
    if container.test_space is not None:
        for p in container.test_space.production():
            if p["cold"]["n"] >= 5 and p["measured_class"] and p["measured_class"] != p["cost"]:
                disagree.append(
                    f"{p['kind']}: configured {p['cost']}, measured {p['measured_class']} (n={p['cold']['n']})"
                )
    add("CFG-TASK-2", "performance", "Configured task costs match measurements", "info",
        "skip" if container.test_space is None else "fail" if disagree else "pass",
        "; ".join(disagree[:5]) or "no disagreement with ≥ 5 cold samples", "configured class = measured class",
        "run a Test Space experiment on the kind and review its proposal", "test_space_profile.md",
        "Test Space → experiment → proposal → accept")  # fmt: skip
    ts = home / "config" / "test-space" / "test-space.yaml"
    sample = []
    if ts.is_file():
        try:
            sample = list((yaml.safe_load(ts.read_text()) or {}).get("sample_runs") or [])
        except yaml.YAMLError:
            sample = []
    add("CFG-TS-1", "performance", "Test Space has sample runs", "info", "pass" if sample else "fail",
        f"{len(sample)} sample run(s)" if ts.is_file() else "no config/test-space/test-space.yaml",
        "a few typical finished runs", "copy test-space.example.yaml and list sample runs", "test_space_profile.md",
        "GET /api/v1/test-space")  # fmt: skip
    # ---- prefetch
    stuck = []
    if container.prefetcher is not None:
        for t in container.prefetcher.list():
            if t.get("stalled") or t.get("state") == "failed":
                stuck.append(f"{t['id']} {t['state']}{' stalled' if t.get('stalled') else ''}")
    add("CFG-PF-1", "prefetch", "No stalled or failed prefetch task", "warning",
        "skip" if container.prefetcher is None else "fail" if stuck else "pass",
        ", ".join(stuck[:5]) or ("checked by the server (mf serve → /api/v1/checks)" if container.prefetcher is None else "none"), "every task completed, stopped by you, or running with bytes flowing",
        "open the task: its diagnosis names the cause and fix; resume to retry", "prefetch_observability_diagnostics.md",
        "GET /api/v1/prefetch/{id} → diagnosis")  # fmt: skip
    # ---- storage
    bad = []
    for role, p in s.paths().items():
        v = validate_path(p, role)
        if v.get("problems"):
            bad.append(f"{role}: {'; '.join(v['problems'])}")
    add("CFG-ST-1", "storage", "Storage folders usable", "critical", "fail" if bad else "pass", "; ".join(bad) or "all roles usable",
        "every role writable with free space", "Settings → Storage: pick another folder", "configuration_check_and_repair.md",
        "GET /api/v1/settings/storage")  # fmt: skip
    # ---- web UI
    from model_forensic.api.app import _web_dist, web_dist_candidates

    web = _web_dist()
    add("CFG-UI-1", "ui", "Web UI is built", "critical", "pass" if web else "fail",
        str(web) if web else "no index.html in " + ", ".join(str(p) for p in web_dist_candidates()[:3]),
        "web/dist/index.html (or MF_WEB_DIST)", "cd web && npm ci && npx vite build, then restart mf serve",
        "configuration_check_and_repair.md", "mf checks; open http://127.0.0.1:8765")  # fmt: skip
    # ---- LLM
    st = getattr(container, "llm_status", None) or {}
    add("CFG-LLM-1", "ai", "Local LLM reachable (optional)", "info",
        "pass" if st.get("state") == "ok" else ("unknown" if not st else "fail"),
        f"{st.get('state')} · {st.get('model')}" if st else (f"address {s.local_llm_url}" if s.local_llm_url else "no address set"),
        "a model answers (deterministic analyses work without one)", "start the server, then the header plug button",
        "configuration_check_and_repair.md", "POST /api/v1/settings/llm/ping")  # fmt: skip
    # ---- privacy
    leaks = [rel for rel in ("config/azure.local.yaml", "config/team.local.yaml", "model-forensic.yaml", "config/tasks.local.yaml")
             if (home / rel).is_file() and _git_ignored(home, rel) is False]  # fmt: skip
    add("CFG-GIT-1", "privacy", "Site configuration is git-ignored", "critical", "fail" if leaks else "pass",
        ", ".join(leaks) or "local files are ignored", "no site file can be committed",
        "add the file to .gitignore (never commit site details)", "configuration_check_and_repair.md",
        "git check-ignore -v <file>")  # fmt: skip
    return out


def summary(checks: list[Check]) -> dict[str, Any]:
    fails = [c for c in checks if c.status == "fail"]
    return {
        "checks": [c.to_dict() for c in checks],
        "failed": len(fails),
        "critical": sum(1 for c in fails if c.severity == "critical"),
        "ok": not any(c.severity == "critical" for c in fails),
    }
