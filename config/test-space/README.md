# Test Space

An isolated place to measure what background tasks cost on *your* runs and backends, test hypotheses about it,
and propose — never silently apply — changes to the master task configuration.

| File | What | In git |
|---|---|---|
| `test-space.example.yaml` | settings template (sample runs, repetitions, thresholds) | yes |
| `test-space.yaml` | your settings | no |
| `proposals/<date>-<kind>-v<N>.yaml` | versioned proposals: current → proposed, statistics, runs, who / when, review | no |

Flow: **experiment** (a task kind on sample runs, cold = caches dropped, warm = straight after; p50 / p90 / p99) →
**hypothesis** verdict (optional: `cost_class`, `warm_speedup`, `under_ms`) → **proposal** (needs `min_samples`
cold samples and a disagreement with the configuration) → **review**: *accept* writes the change to
`config/tasks.local.yaml` (the reviewed overlay of the shipped `defaults/tasks.yaml`) and reloads it; *reject* keeps
the proposal as history. Every step is audited.

UI: **Test Space** in the sidebar. API: `/api/v1/test-space…`. Local-LLM prompts: `setup-agent/prompts/test_space_profile.md`
and `test_space_hypothesis.md`.
