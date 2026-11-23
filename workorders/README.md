# Work orders — how an operator model works on Model Forensic

**Read this first if you are Copilot, Luna or a local model working in the target environment.**
Decision: [ADR-0013](../docs/adr/0013-planner-executor-work-orders.md); why: [design review](../docs/design-review.md).

Claude designed the system but never sees your data, code or configs. You see them. Model Forensic checks what you
write. So you do **small, fully specified tasks** — work orders — and the tool tells you whether the result is right.

## The loop

```
mf work list                          # which task fits (each says when to use it)
mf work show <WO-id>                  # the card: inputs, what to open, what to write, where, how to validate
mf work brief <WO-id> --run <run>     # card + the run's facts, only what the task needs (within its token budget)
   … do the task, write the one output file …
mf work check <WO-id> <file>          # errors → repair once from them
mf work escalate <WO-id> --file <file> --note "what you tried"   # still failing → packet for Claude / Opus
```

## Rules (every work order)

1. **One task, one output file.** Do not do a second task on the side.
2. **Run the input commands; open only the files the card names** (and the code they import).
3. **Fill the template** in the prompt — fields in order, enumerations as listed. Do not add sections.
4. **Never invent** a metric name, config key, class, number or file. Take them from the brief, or cite `file:line`.
   If you cannot find something, leave the field out and say so in `notes`.
5. **Write only inside the card's scope.** Never edit `backend/src/`, `web/src/`, tests, git history, or anything in
   Azure (Model Forensic is read-only towards Azure).
6. **Validate, repair once, then escalate.** The validator's error text is your repair instruction.
7. **Tier**: `local` — you can do it alone; `mid` — needs the repository open (Copilot / Luna); `top` — needs a top
   model (new contracts, code changes): escalate instead of trying.

## Escalation packet

`mf work escalate` writes `<inbox>/escalations/<WO-id>-<time>.md` (≈2k tokens): what was asked, the validator's
errors, your note, your output (trimmed). Give only that file to Claude / Opus — it is enough to fix the output or
the work order. Typical reasons: the contract cannot express what the code does; the fix needs code; the
validator is wrong.

## Adding a work order (for the planner)

Add an entry to `index.yaml` with: `title`, `tier`, `trigger` (when to use it — in the words a developer would use),
`prompt` (the file the operator follows: inputs → steps → output template → scope → validation), `task` (if it uses
a run brief), `inputs`, `reads`, `output {kind, path, schema}`, `scope`, `validate`, `budget_tokens`, `focus`
(config-key and metric-name regexes the brief keeps). `tests/unit/test_workorders.py` checks the entry is complete,
its files exist, its validation command exists and its brief fits the budget.

Write prompts for a 4-bit model: short sentences, numbered steps, one worked example, the output skeleton last.

## Planned: WO-calibrate-questions

The health questions were tuned on synthetic curves. The operator runs `mf questions --run <run> --json` on 10–20
real runs and writes, per problem the tool raised, `{run, question_id, verdict: true|false|unsure, why}` to
`<inbox>/calibration/questions-<date>.yaml`. Model Forensic will propose threshold changes from it (once the
thresholds live in configuration — design review D5); a person accepts them.
