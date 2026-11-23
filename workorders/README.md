# Work orders — how an operator model works on Model Forensic

**Read this first if you are Copilot, Luna or a local model working in the target environment.**
Decision: [ADR-0013](../docs/adr/0013-planner-executor-work-orders.md); why: [design review](../docs/design-review.md).

Claude designed the system but never sees your data, code or configs. You see them. Model Forensic checks what you
write. So you do **small, fully specified tasks** — work orders — and the tool tells you whether the result is right.

## The loop

```
mf work list                          # which task fits (each says when to use it)
mf work show <WO-id>                  # the card: inputs, what to open, what to write, where, how to validate
mf work brief <WO-id> --run <run>     # card + the run's facts: what the task needs first, then everything
   … do the task, write the one output file …
mf work check <WO-id> <file>          # errors → repair once from them
mf work escalate <WO-id> --file <file> --note "what you tried"   # still failing → packet for Claude / Opus
```

## Rules (every work order)

Your limit is judgement, not room: you can read everything, so the rules are about *how* to decide.

1. **One task, one output file.** Do not do a second task on the side.
2. **Run the input commands; open the files the card names first** (and the code they import); read further when
   they are not enough.
2a. **Collect first, decide second.** Pass 1: list every fact you need with its source (brief line or `file:line`).
   Pass 2: fill the output only from that list. Do not stop at the first plausible key — check the brief's other
   candidates (e.g. a weight set in a base config and overridden per stage).
2b. **Tick the card's self-check list** before you validate.
3. **Fill the template** in the prompt — fields in order, enumerations as listed. Do not add sections.
4. **Never invent** a metric name, config key, class, number or file. Take them from the brief, or cite `file:line`.
   If you cannot find something, leave the field out and say so in `notes`.
5. **Write only inside the card's scope.** Never edit `backend/src/`, `web/src/`, tests, git history, or anything in
   Azure (Model Forensic is read-only towards Azure).
6. **Validate, repair once, then escalate.** The validator's error text is your repair instruction.
7. **Tier**: `local` — you can do it alone; `mid` — needs the repository open (Copilot / Luna); `top` — needs a top
   model (new contracts, code changes): escalate instead of trying.

## Escalation packet

`mf work escalate` writes `<inbox>/escalations/<WO-id>-<time>.md`: what was asked, the validator's
errors, your note, your output (trimmed — the expensive model is paid per token). Give only that file to Claude / Opus — it is enough to fix the output or
the work order. Typical reasons: the contract cannot express what the code does; the fix needs code; the
validator is wrong.

## Adding a work order (for the planner)

Add an entry to `index.yaml` with: `title`, `tier`, `trigger` (when to use it — in the words a developer would use),
`prompt` (the file the operator follows: inputs → steps → output template → scope → validation), `task` (if it uses
a run brief), `inputs`, `reads`, `output {kind, path, schema}`, `scope`, `validate`, `focus`
(config-key and metric-name regexes the brief shows first), `checklist` (task-specific self-checks). `tests/unit/test_workorders.py` checks the entry is complete,
its files exist, its validation command exists and its brief loses nothing.

Write prompts for a weaker reasoner, not a smaller window: numbered steps, decisions as tables or enumerations, one
worked example, the traps you know of (the second place a weight is set, a renamed metric), the output skeleton last.

## Planned: WO-calibrate-questions

The health questions were tuned on synthetic curves. The operator runs `mf questions --run <run> --json` on 10–20
real runs and writes, per problem the tool raised, `{run, question_id, verdict: true|false|unsure, why}` to
`<inbox>/calibration/questions-<date>.yaml`. Model Forensic will propose threshold changes from it (once the
thresholds live in configuration — design review D5); a person accepts them.
