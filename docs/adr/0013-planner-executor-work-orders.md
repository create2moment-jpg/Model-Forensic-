# ADR-0013: Planner / executor split — work orders for the operator model

Status: accepted (2026-11)

## Context
Model Forensic's quality depends on knowledge that only exists in the target environment — the real runs, the
training code, its configs and data. The models that can see it there (Copilot with Luna, a local 4-bit Qwen) are
cheap and have all the context they need, but limited judgement; the model that designs the system (Claude) never sees it and is
expensive. Operator instructions had grown in four places with four conventions, briefs were one flat list with no guidance
on what matters for the task or how to decide, and there was no defined way to hand a failure back. See `docs/design-review.md`.

## Decision
* **Claude plans, the operator executes, Model Forensic validates.** Every recurring task in the target environment
  is a **work order** in one registry, `workorders/index.yaml`: trigger, inputs (commands), prompt file, output
  contract (kind, schema, path), scope (allowed paths), validation command, task-specific self-checks, model tier
  (`local` / `mid` / `top`) and escalation rule.
* `mf work list | show | brief | check | escalate` is the single entry point; existing prompt files stay where they are.
* **Briefs guide attention, they never cut**: a work order's `focus` (config-key and metric-name patterns) puts what
  the task is about first; everything else follows, complete (a test checks nothing is lost). The limit of the
  operator is judgement, not context, so every card carries the method — collect facts with their source first,
  fill the template from them second — and a self-check list to tick before validating.
* **Escalation packet**: after two failed validations the operator produces a fixed-format, compact packet (the expensive model is paid per token) for
  an expensive model; nothing else goes back.
* Operators change only the paths in a work order's scope — never `backend/src`, `web/src`, Azure or git history.

## Consequences
+ A weaker reasoner sees one format, one task, one output, a method, a self-check list, and a validator that tells it
  what to fix.
+ New operator tasks are a registry entry plus a prompt; tests check every entry is complete and its files exist.
+ Expensive models are used for contracts and fixes, with a compact packet instead of a long re-explanation.
− The registry must be kept in step with the prompts (a test enforces existence and completeness, not quality).
− Next steps (Observation contract, analysis parameters in config, calibration, API split) are decided but not done.
