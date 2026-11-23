"""Which LLM provider to use: an OpenAI-compatible endpoint when configured, else the offline heuristic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_forensic.application.ports import LLMProvider
from model_forensic.infrastructure.llm.offline import OfflineHeuristicLLM
from model_forensic.infrastructure.llm.openai_compatible import LLMSettings, OpenAICompatibleProvider
from model_forensic.shared.retry import RetryPolicy

if TYPE_CHECKING:
    from model_forensic.settings import LLMConfig


def make_llm(c: LLMConfig, api_key: str | None = None) -> LLMProvider:
    if c.enabled:
        return OpenAICompatibleProvider(
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
                max_concurrency=c.max_concurrency,
            ),
            api_key=api_key,
        )
    return OfflineHeuristicLLM()
