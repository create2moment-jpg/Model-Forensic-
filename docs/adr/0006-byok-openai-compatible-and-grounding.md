# ADR-0006: BYOK via the OpenAI-compatible protocol; deterministic grounding of AI output

Status: accepted

## Context
Users bring their own keys and models (OpenAI, Azure OpenAI, vLLM, Ollama, internal gateways).
LLM output must never introduce invented numbers.

## Decision
A single httpx-based adapter for the OpenAI-compatible chat-completions protocol (tools, JSON mode,
Azure mode). No agent framework. The model must answer with JSON claims (FACT / INFERENCE /
HYPOTHESIS / RECOMMENDATION) citing evidence ids; a deterministic checker verifies citations and
numbers and marks failures UNVERIFIED.

## Consequences
+ Works with any compatible server; no framework lock-in; output quality is measurable (`verified_ratio`).
− Models that are poor at JSON produce more UNVERIFIED claims; acceptable and visible.
