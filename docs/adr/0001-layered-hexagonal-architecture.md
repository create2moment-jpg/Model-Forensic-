# ADR-0001: Layered hexagonal architecture with an explicit epistemic pipeline

Status: accepted

## Context
The product must support multiple ML backends, multiple LLM providers and a growing set of
analyses, and must never let AI interpretation masquerade as measurement.

## Decision
Four layers — domain, analysis (computation), application (orchestration + ports), infrastructure
(adapters) — with dependencies pointing inward. The pipeline DATA → COMPUTATION → INTERPRETATION →
RECOMMENDATION is reflected in code: connectors only retrieve, analysis plugins only compute from
materialized data, the AI layer only interprets computed evidence, and every output carries an
`EpistemicLabel`.

## Consequences
+ Azure ML is one adapter; analyses never see SDK objects; everything is testable without cloud access.
+ Analyses are deterministic and replayable.
− Some duplication between domain models and API payloads (mitigated by pydantic serialization).
