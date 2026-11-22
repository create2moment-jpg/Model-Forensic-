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

## Drafts and resuming work

Unfinished forms are **drafts kept on the server**: the *New study* form and the guided study save as you type, so
you can leave, restart the server or open another browser and continue (*Studies → Drafts → Continue*, or the
Command center's *Continue where you left off*, which also lists open studies with their next step and jobs that did
not finish). Several drafts can exist at once; each has its own link. Drafts are private to you and removed after 90
days without changes.

## The study book

```bash
mf study book <study-id>            # self-contained HTML + study_summary.json
mf study book <study-id> --ai       # + a grounded narrative per chapter
```

The book is organised like a hand-written study:

| Part | Chapters |
|---|---|
| **Preface** | the case file: goal, cast, decision, pre-registration, the verdict up front |
| **Part 1 · The baseline** | the baseline and the team knowledge that applies to it · **how each loss progressed** (raw loss per family per 1 % of training: when the drop happened, still learning / plateaued / rising) · **uncertainty weighting** (effective weight `exp(−2 log σ)` per term over training: which losses σ trusts, which it mutes, and how much of the gradient each really gets) · **the composed configuration against what the run did** (uncertainty switches, task weights vs gradient share, schedule length, LR, decoder depth, submitted vs composed values) |
| **Part 2 · Each run against the baseline** | one chapter per run: what changed in the config, losses over training, where σ moved, where and why the curves diverge (with the config changes that fit the timing), per class on the shared ruler, regressions, *In short*; saved comparisons |
| **Part 3 · Conclusions** | the baseline's and each comparison's conclusions, what the team knowledge set aside (and for which run), what to do next; hypotheses; next steps |
| **Appendix** | pitfall checks P1–P8, shared ruler, convergence with the LR, loss ledger, stage changes by metric family, budget reallocation, capacity, where the model fails, evidence sources |

Without a baseline the first run is studied as the reference. **Team knowledge** (page *Knowledge*) is applied per
run and per stage: what it sets aside (e.g. velocity metrics of a manual-label finetune, the Animal class) is marked
*set aside · entry id* and not counted in verdicts — never silently dropped. With `--ai` (or *AI narrative* in the UI,
e.g. a local server at `127.0.0.1:8080/v1`) each chapter, including the conclusions, gets a grounded narrative:
only claims that cite the chapter's numbers survive; the book never depends on the LLM.
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
