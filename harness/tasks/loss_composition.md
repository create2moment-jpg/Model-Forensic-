# Task: loss composition

For the setup in the brief, explain **how the total training loss is built**.

1. List every loss term that contributes to the logged total: its name **as logged** (match the loss names in the
   brief), the head / sensor / decoder layer it belongs to, its weight (fixed or learned) and the config key that
   enables or weights it.
2. Note terms that exist in code but are disabled for this setup, and terms that are logged but do not reach the
   gradient (e.g. regularisation-only parts).
3. Note auxiliary or denoising terms that are scaffolding rather than the task itself.

Write `fact` entries (one per family of terms, not per term) with code refs, and `caveat` entries where a logged loss is
easy to misread (e.g. "the weighted total can be negative because it includes +log σ").
