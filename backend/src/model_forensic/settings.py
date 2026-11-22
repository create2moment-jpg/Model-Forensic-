"""Configuration (YAML + environment). Secrets are referenced by env var name, never stored."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class BackendConfig(BaseModel):
    kind: str  # local | azureml
    name: str
    title: str | None = None  # display name in the workspace selector
    options: dict[str, Any] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "MF_LLM_API_KEY"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_s: float = 120.0
    context_limit_tokens: int = 64_000
    reasoning_effort: str | None = None
    azure_api_version: str | None = None
    max_tool_steps: int = 4


class ServerConfig(BaseModel):
    """Shared deployment (docs/deploy.md). Defaults keep the single-user local mode."""

    mode: str = "local"  # local | shared
    auth: str = "none"  # none | header | oidc  (local mode ignores it)
    trusted_header: str = "X-Forwarded-Email"  # header mode; App Service Easy Auth: X-MS-CLIENT-PRINCIPAL-NAME
    name_header: str | None = None
    oidc_issuer: str = ""  # e.g. https://login.microsoftonline.com/<tenant>/v2.0
    oidc_audience: str = ""  # the app registration's client id / api://…
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    admins: list[str] = Field(default_factory=list)
    sync_minutes: int = 0  # >0: background incremental sync
    notify_webhook_env: str | None = None  # env var holding a Teams / generic webhook URL (never the URL itself)
    notify_on: list[str] = Field(default_factory=lambda: ["failed", "regression"])
    nightly_books_utc: str | None = None  # "02:30": regenerate the study books of `book_studies` daily
    book_studies: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    data_dir: Path = Path(".mf")
    backends: list[BackendConfig] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    artifact_cache_max_gb: float | None = 200.0
    artifact_min_free_gb: float = 5.0
    download_workers: int = 4
    metric_registry_files: list[Path] = Field(default_factory=list)
    prompt_dirs: list[Path] = Field(default_factory=list)
    plugin_dirs: list[Path] = Field(default_factory=lambda: [Path("config/plugins")])  # your own analyses
    knowledge_dir: Path = Field(default_factory=lambda: Path(os.environ.get("MF_KNOWLEDGE_DIR", "config/knowledge")))
    read_only: bool = True
    log_level: str = "INFO"
    notices: list[str] = Field(default_factory=list)
    me: list[str] = Field(default_factory=list)  # names that identify "me" in runs (from config/azure.local.yaml)
    server: ServerConfig = Field(
        default_factory=ServerConfig
    )  # startup problems shown in diagnostics (not configuration)

    def _merge_azure_interface(self) -> None:
        """Workspaces from the site interface (config/azure.local.yaml or MF_AZURE_* env); see interface/azure.py."""
        from model_forensic.interface import azure

        try:
            iface = azure.load()
        except azure.InterfaceError as exc:
            self.notices.append(f"Azure interface: {exc}")
            return
        if iface.placeholders():
            self.notices.append(
                f"Azure interface: fill in {', '.join(iface.placeholders())} in {iface.source} (placeholders are skipped)"
            )
        have = {b.name for b in self.backends}
        self.backends = [b for b in iface.to_backends() if b.name not in have] + self.backends
        if not self.me:
            self.me = list(iface.defaults.me)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or Path(os.environ.get("MF_CONFIG", "model-forensic.yaml"))
        data: dict[str, Any] = {}
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
        if os.environ.get("MF_DATA_DIR"):
            data["data_dir"] = os.environ["MF_DATA_DIR"]
        s = cls.model_validate(data)
        s._merge_azure_interface()
        if not s.backends:
            s.backends = [BackendConfig(kind="local", name="local", options={"root": str(s.data_dir / "demo-runs")})]
        return s
