"""Azure ML interface: which subscription / resource group / workspaces to read, and how to authenticate.

Filled in by you after checkout — nothing here is committed:

* ``config/azure.local.yaml`` (git-ignored; copy ``config/azure.example.yaml`` or run ``mf init``), or
* environment variables for a single workspace: ``MF_AZURE_SUBSCRIPTION_ID``, ``MF_AZURE_RESOURCE_GROUP``,
  ``MF_AZURE_WORKSPACE`` (optional ``MF_AZURE_WORKSPACE_TITLE``, ``MF_AZURE_TENANT_ID``).

Authentication never uses stored secrets: ``az login`` (default), the default credential chain, or a managed
identity. ``detect()`` asks your local ``az`` CLI for the active subscription, its ML workspaces and the signed-in
user, so ``mf init`` and the Settings page can pre-fill the file for you to confirm.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from model_forensic.settings import BackendConfig

DEFAULT_PATH = Path("config/azure.local.yaml")
_PLACEHOLDER = re.compile(r"^<.*>$|^0{8}-0{4}-0{4}-0{4}-0{12}$|^your[-_]", re.IGNORECASE)
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_AZ_TIMEOUT_S = 30
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class InterfaceError(RuntimeError):
    pass


class AzureDefaults(BaseModel):
    credential: Literal["cli", "default", "managed_identity"] = "cli"
    tenant_id: str | None = None
    metrics_api: Literal["runhistory", "mlflow"] = "runhistory"
    fast_listing: bool = True
    metric_fidelity: Literal["sampled", "full"] = "sampled"  # sampled = what the v1 tool used; full = every point
    sync_max_jobs: int = 1000  # newest jobs indexed per workspace and sync
    me: list[str] = Field(default_factory=list)  # how you appear in runs (display name, e-mail, UPN) — for `user:me`


class AzureWorkspace(BaseModel):
    name: str = Field(description="short id, prefix of run ids (e.g. perception-prod)")
    title: str | None = None
    subscription_id: str
    resource_group: str
    workspace: str
    default: bool = False
    credential: Literal["cli", "default", "managed_identity"] | None = None
    tenant_id: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError("name: lowercase letters, digits, '-' or '_' (max 41 chars)")
        return v

    @property
    def is_placeholder(self) -> bool:
        return any(_PLACEHOLDER.search(str(x)) for x in (self.subscription_id, self.resource_group, self.workspace))

    @property
    def studio_url(self) -> str:
        return (
            f"https://ml.azure.com/?wsid=/subscriptions/{self.subscription_id}/resourcegroups/{self.resource_group}"
            f"/providers/Microsoft.MachineLearningServices/workspaces/{self.workspace}"
        )


class AzureInterface(BaseModel):
    defaults: AzureDefaults = Field(default_factory=AzureDefaults)
    workspaces: list[AzureWorkspace] = Field(default_factory=list)
    source: str | None = None  # where it was loaded from (file path or "env")

    def ready(self) -> list[AzureWorkspace]:
        return [w for w in self.workspaces if not w.is_placeholder]

    def placeholders(self) -> list[str]:
        return [w.name for w in self.workspaces if w.is_placeholder]

    def to_backends(self) -> list[BackendConfig]:
        out = []
        for w in sorted(self.ready(), key=lambda w: not w.default):
            out.append(
                BackendConfig(
                    kind="azureml",
                    name=w.name,
                    title=w.title or w.workspace,
                    options={
                        "subscription_id": w.subscription_id,
                        "resource_group": w.resource_group,
                        "workspace": w.workspace,
                        "credential": w.credential or self.defaults.credential,
                        "tenant_id": w.tenant_id or self.defaults.tenant_id,
                        "metrics_api": self.defaults.metrics_api,
                        "fast_listing": self.defaults.fast_listing,
                        "metric_fidelity": self.defaults.metric_fidelity,
                        "sync_max_jobs": self.defaults.sync_max_jobs,
                    },
                )
            )
        return out

    def public_view(self) -> dict[str, Any]:
        """What the API returns (no credentials exist in here; ids are shown so you can verify them)."""
        return {
            "source": self.source,
            "defaults": self.defaults.model_dump(),
            "workspaces": [
                {**w.model_dump(), "placeholder": w.is_placeholder, "studio_url": w.studio_url} for w in self.workspaces
            ],
        }


def interface_path() -> Path:
    from model_forensic.shared.paths import home_path

    return home_path(os.environ.get("MF_AZURE_CONFIG", str(DEFAULT_PATH)))


def load(path: Path | None = None) -> AzureInterface:
    """File first; the ``MF_AZURE_*`` environment variables add (or override) a single workspace."""
    p = path or interface_path()
    iface = AzureInterface()
    if p.exists():
        doc = yaml.safe_load(p.read_text()) or {}
        try:
            iface = AzureInterface.model_validate({k: v for k, v in doc.items() if k in {"defaults", "workspaces"}})
        except Exception as exc:
            raise InterfaceError(f"{p}: {exc}") from exc
        iface.source = str(p)
    sub, rg, ws = (os.environ.get(f"MF_AZURE_{k}") for k in ("SUBSCRIPTION_ID", "RESOURCE_GROUP", "WORKSPACE"))
    if sub and rg and ws:
        name = re.sub(r"[^a-z0-9_-]", "-", ws.lower())[:41]
        env_ws = AzureWorkspace(
            name=name,
            title=os.environ.get("MF_AZURE_WORKSPACE_TITLE") or ws,
            subscription_id=sub,
            resource_group=rg,
            workspace=ws,
            default=True,
            tenant_id=os.environ.get("MF_AZURE_TENANT_ID"),
        )
        iface.workspaces = [w for w in iface.workspaces if w.name != name] + [env_ws]
        iface.source = f"{iface.source} + env" if iface.source else "env"
    names = [w.name for w in iface.workspaces]
    if len(names) != len(set(names)):
        raise InterfaceError("workspace names must be unique")
    return iface


def save(iface: AzureInterface, path: Path | None = None) -> Path:
    """Atomic write of the local (git-ignored) interface file."""
    p = path or interface_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "defaults": iface.defaults.model_dump(),
        "workspaces": [w.model_dump(exclude_none=True) for w in iface.workspaces],
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        "# Local Azure ML interface (git-ignored). Edit freely or re-run `mf init`.\n"
        + yaml.safe_dump(doc, sort_keys=False)
    )
    tmp.replace(p)
    return p


def set_me(names: list[str], path: Path | None = None) -> Path:
    """Replace ``defaults.me`` only — everything else in the file (and nothing from the environment) is kept."""
    clean = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if len(clean) > 20 or any(len(n) > 120 for n in clean):
        raise InterfaceError("at most 20 names of up to 120 characters")
    p = path or interface_path()
    doc: dict[str, Any] = {}
    if p.exists():
        doc = yaml.safe_load(p.read_text()) or {}
    doc.setdefault("defaults", {})
    doc["defaults"]["me"] = clean
    doc.setdefault("workspaces", [])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        "# Local Azure ML interface (git-ignored). Edit freely or re-run `mf init`.\n"
        + yaml.safe_dump(doc, sort_keys=False)
    )
    tmp.replace(p)
    return p


# ------------------------------------------------------------------------------------------ az CLI detection
def run_az(args: list[str]) -> Any:
    """Run the local ``az`` CLI (no shell) and parse its JSON output."""
    exe = shutil.which("az")
    if exe is None:
        raise InterfaceError("Azure CLI ('az') not found on PATH — install it or fill config/azure.local.yaml by hand")
    try:
        # Fixed executable, argument list, no shell; the only caller-supplied value (subscription id) is validated.
        done = subprocess.run(  # noqa: S603
            [exe, *args, "-o", "json"], capture_output=True, text=True, timeout=_AZ_TIMEOUT_S, check=False
        )
    except FileNotFoundError as exc:
        raise InterfaceError(
            "Azure CLI ('az') not found on PATH — install it or fill config/azure.local.yaml by hand"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise InterfaceError(f"az {' '.join(args[:2])} timed out after {_AZ_TIMEOUT_S}s") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "unknown error").strip().splitlines()
        raise InterfaceError(f"az {' '.join(args[:2])} failed: {detail[-1] if detail else 'unknown error'}")
    try:
        return json.loads(done.stdout or "null")
    except json.JSONDecodeError as exc:
        raise InterfaceError("az returned non-JSON output") from exc


def detect(subscription_id: str | None = None) -> dict[str, Any]:
    """Active subscription, its Azure ML workspaces and the signed-in user, as suggestions to confirm."""
    account = run_az(["account", "show"])
    if not isinstance(account, dict) or not account.get("id"):
        raise InterfaceError("no active subscription — run `az login`")
    sub = subscription_id or str(account["id"])
    if not _UUID.match(sub):
        raise InterfaceError("subscription id must be a GUID")
    found = (
        run_az(
            [
                "resource",
                "list",
                "--subscription",
                sub,
                "--resource-type",
                "Microsoft.MachineLearningServices/workspaces",
                "--query",
                "[].{workspace:name, resource_group:resourceGroup, location:location}",
            ]
        )
        or []
    )
    user: dict[str, Any] = {}
    try:
        u = run_az(["ad", "signed-in-user", "show", "--query", "{name:displayName, mail:mail, upn:userPrincipalName}"])
        user = u if isinstance(u, dict) else {}
    except InterfaceError:
        user = {"name": (account.get("user") or {}).get("name")}
    workspaces = [
        {
            "name": re.sub(r"[^a-z0-9_-]", "-", str(w["workspace"]).lower())[:41],
            "title": str(w["workspace"]),
            "subscription_id": sub,
            "resource_group": str(w["resource_group"]),
            "workspace": str(w["workspace"]),
            "location": w.get("location"),
        }
        for w in found
        if isinstance(w, dict) and w.get("workspace")
    ]
    return {
        "subscription_id": sub,
        "subscription_name": account.get("name"),
        "tenant_id": account.get("tenantId"),
        "user": user,
        "workspaces": workspaces,
    }
