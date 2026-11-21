# FAQ

**Does it change anything in Azure ML?** No. Every call is a list / get / read; there is no code path that submits,
tags or deletes. Downloads go to a local cache with a disk budget.

**What goes into git?** Code and examples only. Subscription ids, workspace names, tenant, hostnames live in
`config/*.local.yaml` (git-ignored); secrets (LLM keys, webhook URLs) only in environment variables.

**Does it need an LLM?** No. Every analysis, check, verdict and the study book are deterministic. An LLM (any
OpenAI-compatible endpoint, including local models) adds narratives, focused questions and next-experiment
proposals — only claims that cite evidence and use its numbers are kept.

**Why does it say my 0.40 vs 0.55 gap is not real?** The two runs are scored with different rulers (P1). Compare the
per-class, ruler-free table in the study book instead.

**A finding is wrong. What now?** Click *Is this finding right? → 👎* and say why. Diagnostics shows the false-alarm
rate per analysis; thresholds are team-policy parameters (see [calibration](../calibration.md)).

**It cannot connect.** `mf doctor --live` (or *Test connection* in Settings) names the cause and the fix;
`--bundle support.json` writes a redacted file you can hand to a colleague or a coding LLM with `AGENTS.md`.
[Troubleshooting](../troubleshooting.md).

**Can my team share it?** Yes: shared mode with company sign-in, background sync, alerts and nightly study books
([deploy](../deploy.md)).

**How big can it get?** Tested with 2,000 runs and a run with 5 million metric points; search stays around 10 ms.
