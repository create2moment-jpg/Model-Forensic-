"""Target-setup compatibility report: what Model Forensic needs, what this machine and project provide.

The deterministic half of the setup protocol (``setup-agent/``): DISCOVER what exists, DECLARE every precondition,
CHECK each one — ``satisfied`` / ``missing`` / ``incompatible`` / ``unknown`` / ``optional`` — and say, for each gap,
which file in the *permitted* adaptation area would close it. Nothing here changes anything; with ``live`` it also
runs the adapter contract (read-only) against every project's workspaces.

The report is machine-readable (JSON) and human-readable, and is recorded locally (git-ignored) with the versions
it was made with, so the effective setup is traceable without committing private details.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from model_forensic import __version__
from model_forensic.shared.paths import describe, mf_home

Status = Literal["satisfied", "missing", "incompatible", "unknown", "optional"]

# Where a coding agent may make target-specific changes (relative to the Model Forensic folder). Everything else —
# backend/src, web/src, harness/, setup-agent/, tests/ — is the fixed core: read it, never edit it for one project.
ALLOWED_PATHS = (
    "model-forensic.yaml",
    "config/azure.local.yaml",
    "config/team.local.yaml",
    "config/projects/*.local.yaml",
    "config/knowledge/",
    "config/plugins/",
    "config/setup/",
)
FORBIDDEN_PATHS = ("backend/src/", "web/src/", "harness/", "setup-agent/", "backend/tests/", "web/e2e/")


@dataclass
class Requirement:
    id: str
    area: str
    title: str
    status: Status
    detail: str = ""
    fix: str = ""
    required: bool = True


@dataclass
class Report:
    generated_at: str
    model_forensic: str
    contract_version: int
    python: str
    platform: str
    roots: dict[str, str]
    allowed_paths: tuple[str, ...] = ALLOWED_PATHS
    forbidden_paths: tuple[str, ...] = FORBIDDEN_PATHS
    requirements: list[Requirement] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocking(self) -> list[Requirement]:
        return [r for r in self.requirements if r.required and r.status in ("missing", "incompatible")]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = not self.blocking
        d["counts"] = {s: sum(1 for r in self.requirements if r.status == s) for s in Status.__args__}  # type: ignore[attr-defined]
        return d


def _writable(p: Path) -> tuple[bool, str]:
    target = p if p.exists() else next((q for q in p.parents if q.exists()), p)
    ok = os.access(target, os.W_OK)
    return ok, str(target)


def _git_ignored(home: Path, rel: str) -> bool | None:
    git = shutil.which("git")
    if git is None or not (home / ".git").exists():
        return None
    r = subprocess.run([git, "-C", str(home), "check-ignore", "-q", rel], capture_output=True, check=False)  # noqa: S603
    return r.returncode == 0


def check(settings: Any, service: Any = None, live: bool = False) -> Report:
    from model_forensic.projects import CONTRACT_VERSION, load_profiles

    home = mf_home()
    roots = describe()
    rep = Report(
        generated_at=datetime.now(timezone.utc).isoformat(),
        model_forensic=__version__,
        contract_version=CONTRACT_VERSION,
        python=platform.python_version(),
        platform=f"{platform.system()} {platform.machine()}",
        roots=roots,
    )
    add = rep.requirements.append

    # --- runtime ---------------------------------------------------------------------------------------------
    add(
        Requirement(
            "R1",
            "runtime",
            "Python 3.11 or newer",
            "satisfied" if sys.version_info >= (3, 11) else "incompatible",
            platform.python_version(),
            "" if sys.version_info >= (3, 11) else "use Python ≥ 3.11 for the venv",
        )
    )
    azure = all(importlib.util.find_spec(m) is not None for m in ("azure.ai.ml", "azure.identity"))
    uses_azure = any(b.kind == "azureml" for b in settings.backends)
    add(
        Requirement(
            "R2",
            "runtime",
            "Azure ML packages (when a workspace is Azure ML)",
            "satisfied" if azure else ("missing" if uses_azure else "optional"),
            "installed" if azure else "not installed",
            "" if azure else "pip install -e 'backend[azure]'",
            required=uses_azure,
        )
    )
    az = shutil.which("az")
    add(
        Requirement(
            "R3",
            "runtime",
            "Azure CLI for `az login` credentials",
            "satisfied" if az else ("missing" if uses_azure else "optional"),
            az or "not on PATH",
            "" if az else "install the Azure CLI, or set credential: managed_identity",
            required=False,
        )
    )
    dist = Path(os.environ.get("MF_WEB_DIST", str(home / "web" / "dist")))
    add(
        Requirement(
            "R4",
            "runtime",
            "Built web UI",
            "satisfied" if (dist / "index.html").is_file() else "missing",
            str(dist),
            "cd web && npm ci && npx vite build",
            required=False,
        )
    )

    # --- paths -----------------------------------------------------------------------------------------------
    pp = roots["project_root_from"] != "current directory"
    add(
        Requirement(
            "P1",
            "paths",
            "PROJECT_PATH points at the project root",
            "satisfied" if pp else "unknown",
            f"{roots['project_root']} ({roots['project_root_from']})",
            "" if pp else "scripts/set-project-path.sh /path/to/project (writes ~/.bashrc and ~/.zshrc)",
            required=False,
        )
    )
    for rid, title, p in (
        ("P2", "data folder writable", Path(settings.data_dir)),
        ("P3", "knowledge inbox writable", Path(settings.knowledge_dir)),
    ):
        ok, where = _writable(p)
        add(
            Requirement(
                rid,
                "paths",
                title,
                "satisfied" if ok else "incompatible",
                where,
                "" if ok else f"grant write access to {where}",
            )
        )
    try:
        free = shutil.disk_usage(_writable(Path(settings.data_dir))[1]).free
        status: Status = "satisfied" if free > 5 * 1024**3 else "incompatible"
        add(
            Requirement(
                "P4",
                "paths",
                "at least 5 GB free for caches",
                status,
                f"{free / 1e9:.1f} GB free",
                "" if status == "satisfied" else "free space or move data_dir (model-forensic.yaml)",
            )
        )
    except OSError as exc:
        add(Requirement("P4", "paths", "at least 5 GB free for caches", "unknown", str(exc)))

    # --- configuration (the permitted, git-ignored area) --------------------------------------------------------
    azure_cfg = home / "config" / "azure.local.yaml"
    add(
        Requirement(
            "C1",
            "configuration",
            "workspaces configured",
            "satisfied" if settings.backends else "missing",
            ", ".join(f"{b.name} ({b.kind})" for b in settings.backends) or "none",
            "mf init (writes config/azure.local.yaml) or a local backend in model-forensic.yaml",
        )
    )
    for rel in (
        "config/azure.local.yaml",
        "config/team.local.yaml",
        "config/projects/x.local.yaml",
        "model-forensic.yaml",
    ):
        ign = _git_ignored(home, rel)
        if ign is None:
            continue
        add(
            Requirement(
                f"G-{rel}",
                "privacy",
                f"{rel.replace('x.local', '*.local')} is git-ignored",
                "satisfied" if ign else "incompatible",
                "ignored" if ign else "would be committed",
                "" if ign else f"add {rel} to .gitignore — private details must not reach the repository",
            )
        )
    profiles, notes = load_profiles(Path(settings.projects_dir))
    add(
        Requirement(
            "C2",
            "configuration",
            "project profiles valid",
            "satisfied" if not notes else "incompatible",
            f"{len(profiles)} loaded" + (f"; problems: {'; '.join(notes[:3])}" if notes else ""),
            "fix the listed config/projects/*.local.yaml" if notes else "",
            required=bool(notes),
        )
    )
    if not profiles:
        add(
            Requirement(
                "C3",
                "configuration",
                "a project profile for the target",
                "optional",
                "none",
                "copy config/projects/<kind>.example.yaml to <id>.local.yaml",
                required=False,
            )
        )
    add(
        Requirement(
            "C4",
            "configuration",
            "LLM provider (optional — everything deterministic works without)",
            "satisfied" if settings.llm.enabled else "optional",
            f"{settings.llm.model} at {settings.llm.base_url}" if settings.llm.enabled else "off",
            "" if settings.llm.enabled else "Settings → LLM, or the plug in the header",
            required=False,
        )
    )
    if azure_cfg.is_file():
        add(Requirement("C5", "configuration", "Azure interface file present", "satisfied", str(azure_cfg)))

    # --- projects ---------------------------------------------------------------------------------------------
    names = {b.name for b in settings.backends}
    for prof in profiles:
        missing = [b for b in prof.backends if b not in names]
        entry: dict[str, Any] = {"id": prof.id, "workspaces": prof.backends, "missing_workspaces": missing}
        add(
            Requirement(
                f"J-{prof.id}",
                "projects",
                f"project {prof.id}: workspaces configured",
                "satisfied" if not missing else "missing",
                ", ".join(prof.backends) or "none",
                f"add {', '.join(missing)} to config/azure.local.yaml or local_roots" if missing else "",
            )
        )
        if live and service is not None:
            from model_forensic.projects.contract import check_run_source, summary

            entry["contract"] = {}
            for b in prof.backends:
                src = service.sources.get(b)
                if src is None:
                    continue
                s = summary(check_run_source(src))
                entry["contract"][b] = s
                bad = [r for r in s["results"] if not r["ok"] and r["required"]]
                add(
                    Requirement(
                        f"K-{prof.id}-{b}",
                        "contract",
                        f"{prof.id} / {b}: adapter contract v{CONTRACT_VERSION}",
                        "satisfied" if not bad else "incompatible",
                        f"{s['passed']} checks passed"
                        if not bad
                        else "; ".join(f"{r['id']} {r['detail']}" for r in bad[:3]),
                        ""
                        if not bad
                        else "fix the workspace / layout config, or report an interface gap (docs/projects.md)",
                    )
                )
        rep.projects.append(entry)
    for r in rep.requirements:
        if r.status == "satisfied":
            r.fix = ""
    return rep


def text(rep: Report) -> str:
    icon = {"satisfied": "✓", "missing": "✗", "incompatible": "✗", "unknown": "?", "optional": "·"}
    lines = [
        f"Model Forensic {rep.model_forensic} · contract v{rep.contract_version} · Python {rep.python} · {rep.platform}",
        f"project root {rep.roots['project_root']} ({rep.roots['project_root_from']}) · home {rep.roots['mf_home']}",
        "",
    ]
    area = None
    for r in rep.requirements:
        if r.area != area:
            lines.append(f"[{r.area}]")
            area = r.area
        lines.append(
            f"  {icon[r.status]} {r.status:<12} {r.title} — {r.detail}" + (f"\n      fix: {r.fix}" if r.fix else "")
        )
    lines += [
        "",
        "Blocking: " + (", ".join(r.id for r in rep.blocking) if rep.blocking else "none"),
        "Changes allowed only in: " + ", ".join(rep.allowed_paths),
    ]
    return "\n".join(lines)


def record(rep: Report, home: Path | None = None) -> Path:
    """Keep the report locally (git-ignored config/setup/): the effective setup, without committing private details."""
    d = (home or mf_home()) / "config" / "setup"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "compatibility.local.json"
    p.write_text(json.dumps(rep.to_dict(), indent=2, default=str), encoding="utf-8")
    return p
