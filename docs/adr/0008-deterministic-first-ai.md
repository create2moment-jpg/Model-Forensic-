# ADR-0008: Deterministic analyses run before, and feed, the AI

Status: accepted

## Context
"AI should interpret evidence, not replace measurement."

## Decision
Every investigation first runs the deterministic analysis packs, then builds a bounded evidence
context from their findings, comparisons and config diffs. The LLM can call READ tools for more
data; each tool result becomes citable evidence. Expensive tools require user confirmation;
mutating tools are disabled.

## Consequences
+ AI answers are anchored in reproducible computation; an offline heuristic interpreter can replace the LLM.
− Investigations cost an analysis pass (sub-second on typical runs).
