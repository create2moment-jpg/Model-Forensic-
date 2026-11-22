# ADR-0011: Knowledge from experience is a belief, checked against the runs

Status: accepted

## Context
People carry knowledge from experience ("a higher learning rate hurts the finetune", "σ mutes the radar head").
It is valuable and often right, but it is not a fact: it was formed on other runs, other data, other code. Treating
it as fact would let the tool repeat a wrong belief with authority; ignoring it wastes what the team knows.

## Decision
* Experience is stored as knowledge entries of `kind: belief` (same files, same review) with an optional **test**:
  `config_effect` (changing a key moves a metric up / down / not at all) or `loss_behaviour` (a loss family is
  muted / trusted / done early / still learning / rising), and a **scope** (a run search). Beliefs never set
  anything aside; they are checked and reported.
* **Evidence**: for `config_effect`, runs in scope are paired so each pair differs in the key with as few other
  meaningful settings as possible (seeds, paths, names, ids are noise); each run is used once; weight
  `1 / (1 + other differences)`, pairs with more than 20 other differences are skipped and counted. A change smaller
  than `max(min_effect, 2 × seed noise)` is *no effect* (the seed noise comes from runs that differ only in noise
  keys). Pipeline stages are not counted separately from their pipeline. For `loss_behaviour`, each run with the
  loss / σ series is one observation.
* **Probability**: Beta posterior, `α = 1 + prior + Σ support`, `β = 1 + Σ against + ½ Σ no-effect`. The person's
  confidence is a weak prior (0 / ½ / 1 pseudo-observation for low / medium / high — never more than one clean
  pair), and the evidence-only estimate is always shown next to it.
* **Colour**: dark green = p ≥ 0.85, 10th percentile ≥ 0.6 and ≥ 4 weighted observations; green ≥ 0.65;
  red ≤ 0.35; yellow otherwise; grey = no test or no usable comparison.
* Every verdict explains itself: counts, interval, the strongest counterexample, the experiment that would settle
  it, the evidence table, and the history of past verdicts (a changed test marks the verdict stale).
* **Used in analysis**: the study book checks each belief on every comparison whose runs differ in the belief's key
  (*Your experience, checked on this comparison*).

## Consequences
+ Experience becomes measurable without being trusted blindly; disagreement shows up as a red bubble with the
  counterexample, agreement as green only when the runs say so.
− Observational evidence, not controlled experiments: confounders that are not in the config (code changes,
  data drift) are invisible — the weighting and the "what would settle it" experiment address this, they do not
  remove it.
− Free-text beliefs stay grey until someone adds a test (an LLM could propose one later; the check stays
  deterministic).
