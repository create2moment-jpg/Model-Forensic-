# Studies: from a question to a decision

A study answers one question with several runs — and refuses to answer it while the measurement is broken.

## The guided path

**Studies → Guided study** (or `/study-wizard`):

1. **Question** — pick a template: *Is my change better?*, *Why did it regress?*, *Should I train longer?*,
   *Is this head useful?*, *Pretrain vs finetune*. Each comes with a falsifiable claim, the confounders to rule out
   and the analyses to run.
2. **Runs** — candidate(s) and baseline. Seed replicates (same `group` tag) give a noise floor.
3. **Pre-register** — the deciding metric and what would falsify the claim, written *before* you look.
4. **Decide** — the gate runs the measurement checks first. If **P1** (different rulers), **P2** (metric meaning),
   **P3** (degenerate ground truth) or **P6** (untraceable figure) fails, adopt/reject is blocked unless you give an
   override reason. The decision (adopt / reject / more evidence / inconclusive) is recorded with who decided.

## The study book

```bash
mf study book <study-id>            # self-contained HTML + study_summary.json
mf study book <study-id> --ai       # + a grounded narrative per chapter
```

Chapters: cover → verdict up front → pitfall checks P1–P8 → the shared ruler (per-class, ruler-free) →
convergence (plateau in epochs, read with the LR) → what each stage changed, by metric family → capacity →
where the model fails → hypotheses → loss ledger and budget reallocation → next steps → evidence sources.
*Print / save as PDF* is built in. [What it reproduces of a hand-written study](../study-reproduction.md).

## Good habits the tool enforces

| Pitfall | Check |
|---|---|
| Comparing headline scores computed with different rulers | P1 |
| Reading a metric by its nickname | P2 |
| Error metrics on ground truth that is constant (e.g. zeroed velocity) | P3 |
| "Best" checkpoint chosen on a metric that is never logged | P4 |
| No validation metrics at all | P5 |
| A figure that cannot be traced to data | P6 |
| "Radar is useless" when only its auxiliary head is muted | P7 |
| "Train longer" when the loss stopped at high LR | P8 |
