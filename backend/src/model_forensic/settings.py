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


class Settings(BaseModel):
    data_dir: Path = Path(".mf")
    backends: list[BackendConfig] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    artifact_cache_max_gb: float | None = 200.0
    artifact_min_free_gb: float = 5.0
    download_workers: int = 4
    metric_registry_files: list[Path] = Field(default_factory=list)
    prompt_dirs: list[Path] = Field(default_factory=list)
    read_only: bool = True
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or Path(os.environ.get("MF_CONFIG", "model-forensic.yaml"))
        data: dict[str, Any] = {}
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
        if os.environ.get("MF_DATA_DIR"):
            data["data_dir"] = os.environ["MF_DATA_DIR"]
        s = cls.model_validate(data)
        if not s.backends:
            s.backends = [BackendConfig(kind="local", name="local", options={"root": str(s.data_dir / "demo-runs")})]
        return s
