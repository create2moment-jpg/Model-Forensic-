# Task: uncertainty weighting

Find whether and how losses are **weighted by learned uncertainty** (e.g. Kendall-style log σ per task / head).

1. Which loss terms are uncertainty-weighted, and which are fixed-weight?
2. The exact formula (e.g. `exp(-2σ)·L + σ`), the logged names of the weighted / unweighted / no-regularisation losses
   and of σ (match the brief), σ initialisation, clamps or bounds, and any per-term overrides in the config.
3. Anything that makes a logged value easy to misread (negative weighted loss, σ hitting a clamp, a head whose σ runs
   away = the model muting it).

Write `fact` and `caveat` entries with code refs; add `when.setup` if it differs per setup.
