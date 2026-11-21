"""Composition root: the only place that wires adapters to ports."""

from __future__ import annotations

from dataclasses import dataclass

from model_forensic.ai.fake_llm import OfflineHeuristicLLM
from model_forensic.ai.investigator import Investigator
from model_forensic.ai.prompts import PromptLibrary
from model_forensic.analysis.metric_registry import MetricRegistry
from model_forensic.application.ports import LLMProvider
from model_forensic.application.service import ForensicService
from model_forensic.infrastructure.artifacts.store import ArtifactStore
from model_forensic.infrastructure.connectors import create_connector
from model_forensic.infrastructure.llm.openai_compatible import LLMSettings, OpenAICompatibleProvider
from model_forensic.infrastructure.persistence.sqlite_store import (
    DocumentStore,
    SqliteFindingStore,
    SqliteWorkspaceStore,
)
from model_forensic.infrastructure.search.sqlite_index import SqliteSearchIndex
from model_forensic.settings import Settings
from model_forensic.shared.retry import RetryPolicy


@dataclass
class Container:
    settings: Settings
    service: ForensicService
    docs: DocumentStore
    prompts: PromptLibrary
    llm: LLMProvider

    def investigator(self) -> Investigator:
        return Investigator(
            self.service,
            self.llm,
            self.prompts,
            max_steps=self.settings.llm.max_tool_steps,
            token_budget=min(24_000, self.settings.llm.context_limit_tokens // 2),
        )


def build(settings: Settings) -> Container:
    d = settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    metrics = MetricRegistry()
    for f in settings.metric_registry_files:
        metrics.load_yaml(f)
    docs = DocumentStore(d / "forensic.sqlite")
    svc = ForensicService(
        sources=[create_connector(b.kind, {"name": b.name, **b.options}) for b in settings.backends],
        index=SqliteSearchIndex(d / "index.sqlite"),
        findings=SqliteFindingStore(docs),
        workspaces=SqliteWorkspaceStore(docs),
        artifacts=ArtifactStore(
            d / "artifacts",
            max_bytes=int(settings.artifact_cache_max_gb * 1024**3) if settings.artifact_cache_max_gb else None,
            min_free_bytes=int(settings.artifact_min_free_gb * 1024**3),
            workers=settings.download_workers,
        ),
        metrics=metrics,
    )
    llm: LLMProvider
    c = settings.llm
    if c.enabled:
        llm = OpenAICompatibleProvider(
            LLMSettings(
                base_url=c.base_url,
                model=c.model,
                api_key_env=c.api_key_env,
                temperature=c.temperature,
                max_tokens=c.max_tokens,
                timeout_s=c.timeout_s,
                context_limit_tokens=c.context_limit_tokens,
                reasoning_effort=c.reasoning_effort,
                azure_api_version=c.azure_api_version,
                retry=RetryPolicy(max_attempts=3),
            )
        )
    else:
        llm = OfflineHeuristicLLM()
    return Container(
        settings=settings, service=svc, docs=docs, prompts=PromptLibrary.load(settings.prompt_dirs), llm=llm
    )
