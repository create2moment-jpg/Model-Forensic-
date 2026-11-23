# ADR-0013: Planner / executor split — work orders for the operator model

Status: accepted (2026-11)

## Context
Model Forensic's quality depends on knowledge that only exists in the target environment — the real runs, the
training code, its configs and data. The models that can see it there (Copilot with Luna, a local 4-bit Qwen) are
cheap but have limited reasoning and context; the model that designs the system (Claude) never sees it and is
expensive. Operator instructions had grown in four places with four conventions, briefs carried the whole run
regardless of the task, and there was no defined way to hand a failure back. See `docs/design-review.md`.

## Decision
* **Claude plans, the operator executes, Model Forensic validates.** Every recurring task in the target environment
  is a **work order** in one registry, `workorders/index.yaml`: trigger, inputs (commands), prompt file, output
  contract (kind, schema, path), scope (allowed paths), validation command, token budget, model tier
  (`local` / `mid` / `top`) and escalation rule.
* `mf work list | show | brief | check | escalate` is the single entry point; existing prompt files stay where they are.
* **Briefs are scoped**: a work order's `focus` (config-key and metric-name patterns) limits what the harness brief
  carries; the budget is checked by a test.
* **Escalation packet**: after two failed validations the operator produces a fixed-format packet (≤2k tokens) for
  an expensive model; nothing else goes back.
* Operators change only the paths in a work order's scope — never `backend/src`, `web/src`, Azure or git history.

## Consequences
+ A small model sees one format, one task, one output, and a validator that tells it what to fix.
+ New operator tasks are a registry entry plus a prompt; tests check every entry is complete and its files exist.
+ Expensive models are used for contracts and fixes, with a compact packet instead of a long re-explanation.
− The registry must be kept in step with the prompts (a test enforces existence and completeness, not quality).
− Next steps (Observation contract, analysis parameters in config, calibration, API split) are decided but not done.
