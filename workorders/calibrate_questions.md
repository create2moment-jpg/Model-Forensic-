# Work order WO-calibrate-questions — judge the tool's alarms on real runs

**Goal**: tell Model Forensic which of its training-health alarms were right on *your* runs. You judge; you never set
a number. The tool turns your verdicts into proposed thresholds, and a person accepts them.

## Inputs

1. Pick 10–20 real runs you know: some healthy, some with known problems (diverged, plateaued, a dead branch…).
2. `mf calibrate template --runs <id>,<id>,… [--stage <stage>]` — writes
   `<inbox>/calibration/questions-<date>.yaml`: one item per alarm, with what the tool measured (`measure`), the
   threshold it was compared with (`param`) and its sentence (`answer`).

## Steps — for every item

Pass 1, collect (do not judge yet). For the item's run and question, look at:
- the curve(s) the question is about (Metrics tab, or `mf questions --run <run> --json` → `keys`);
- what you know about the run (the ledger, the run's notes, the knowledge entries);
- for loss / weight questions, the config and code of that run if the answer depends on them.
Write down, in one line each, what you saw and where.

Pass 2, decide — use this table:

| What you saw | verdict |
|---|---|
| The run really has this problem (you would want to be warned) | `true` |
| The run is fine here, the alarm is wrong (e.g. a term *meant* to dominate, a plateau *meant* to happen at a fixed LR) | `false` — say why |
| You cannot tell from the curves, the code or what you know | `unsure` — say what is missing |

Then, `missed`: problems you know these runs have that the tool did **not** raise — `{run, question, why}`, with
`question` one of the ids on the file's last line (`# questions: …`).

## Output

The same file, with only `verdict` and `why` filled (and `missed`). Do not change `measure`, `param`, `status`,
`answer` — they are what the tool measured.

```yaml
items:
  - run: local:run-17
    question: term_share
    status: warn
    answer: train/loss/velocity is 72% of the summed terms at the end — it drives the gradient.
    measure: 0.72
    param: loss_questions.term_share
    alarm_when: above
    verdict: "false"
    why: velocity has weight 1.0 by design in this config (configs/base.py:88); 70–75% is normal here
missed:
  - run: local:run-21
    question: plateau
    why: the loss is flat from 40% of training (LR stays at its peak) and the tool said ok
```

## Scope

`<inbox>/calibration/` only.

## Validation

`mf calibrate check <file>` → complete. Then a person runs `mf calibrate propose <file>` (and `mf calibrate apply`
after reviewing the proposal).

## Before you validate, check

- [ ] Every item has `verdict` true / false / unsure; every false / unsure has a `why`
- [ ] Every `why` names what you looked at (a curve, a config key, a `file:line`, a ledger entry)
- [ ] You did not change measure / param / status / answer
- [ ] `missed` uses question ids from the file's `# questions:` line
