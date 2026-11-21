# Requirements critique

The master prompt is strong on scope and principles. Before designing, these are the gaps and
risks I found and how the design responds. Items marked **→ changed** altered the design.

## Scientific-validity risks (highest impact)

| # | Risk in the spec | Why it matters | Response |
|---|---|---|---|
| S1 | "Statistically meaningful differences where applicable" is underspecified. Most Azure ML comparisons are **single-seed**. | A −1% NDS delta between two single runs is usually seed noise. A forensic tool that flags it as a regression trains engineers to chase noise. | **→ changed.** Replicate groups (`tags.group`) are first-class. With ≥2 replicates, verdicts use a z-score against measured seed variance *and* a practical-significance floor. Without them, every regression finding carries an explicit limitation and confidence is capped at MEDIUM. |
| S2 | "Identify likely causal candidates" from config diffs. | With one baseline/candidate pair, all changed parameters are perfectly confounded. Any ranking is a prior, not evidence. | **→ changed.** `config_attribution` only emits `HYPOTHESIZED` findings, says "confounded" when >1 change, and its main output is the **single-variable ablation** that would separate the hypotheses. |
| S3 | Aggregate metrics hide slice regressions (the spec's own "degrades beyond 60 m" example). | Headline NDS can be flat while long-range recall collapses. | **→ changed.** Metric keys carry **dimensions** (`recall[range=60-inf]`). Regression analysis localizes along every dimension and reports *hidden* slice regressions under a flat aggregate. |
| S4 | "Modality contribution" is only quantifiable with controlled ablations. | Comparing a camera-only run with an unrelated fused run conflates modality with everything else. | Modality analysis is scoped to *ablation sets* (same config except modality) and is planned for Phase 4; until then, range/modality-sliced evaluation is used and the report says so. |
| S5 | Multiple comparisons: dozens of metrics × slices tested at once. | Some "significant" deltas are expected by chance. | Findings are ranked by effect size, not p-value; replicate-based verdicts need both z ≥ 2 and a practical minimum change. A Holm/BH correction is listed for Phase 3 when per-sample eval data is available. |
| S6 | Eval metrics logged at the last step vs. the best checkpoint. | Comparing last-vs-best across runs is a classic hidden bias. | `MetricSeries` keeps steps; summaries use `LAST` explicitly; overfitting analysis recommends evaluating the min-val checkpoint. "Best-checkpoint comparison mode" is a Phase 3 item. |

## AI risks

| # | Risk | Response |
|---|---|---|
| A1 | "Never allow the AI to invent metrics" is a requirement, not a mechanism. | **→ changed.** Deterministic **grounding checker**: every claim must cite evidence ids; every number in a FACT/INFERENCE claim must appear in the cited evidence (tolerance for rounding/percent forms). Failing claims are shown as UNVERIFIED, never silently dropped or promoted. |
| A2 | Logs/configs/artifacts are attacker-controllable (any teammate, any dependency printing to stdout). | Untrusted text is fenced with a per-session nonce, fence tokens are neutralized inside the content, the system prompt forbids following instructions inside fences, and tools are READ-only by default. Prompt injection cannot trigger downloads or mutations without user confirmation. |
| A3 | "Agentic workflow" can loop and burn tokens. | Hard cap on tool steps; size-capped tool results; JSON final answer forced on the last step. |
| A4 | Without an API key the AI layer is untestable. | An offline, clearly-labelled heuristic interpreter produces grounded claims from deterministic findings so the full pipeline runs in CI and demo mode. |

## Architecture / scalability risks

| # | Risk | Response |
|---|---|---|
| R1 | "Millions of runs" + live Azure queries per keystroke is impossible; Azure ML list APIs are slow and throttled. | **→ changed.** A local **search index** (SQLite + FTS5 trigram, ADR-0004) is the read path; backends are synced incrementally in the background. The UI never blocks on Azure. |
| R2 | Metric histories can be millions of points per run. | Series cached per terminal run; API downsamples (`max_points`); analyses run on numpy arrays. Columnar (Parquet) cache is the Phase 3 upgrade path. |
| R3 | Multi-GB checkpoints. | Download **profiles** decide what is fetched; resumable `.part` files; content-addressed dedup; disk-space guard; LRU retention. MLflow's artifact API is whole-file, so true byte-range resume for Azure needs direct Blob access (Phase 2b). |
| R4 | "Plugin architecture for everything" risks premature abstraction. | Plugin seams exist only where a second implementation is certain: connectors, analyses, LLM providers, prompt packs, metric registry. Visualization/report plugins wait for a second real use case. |
| R5 | Concurrency "eventually". | Modular monolith (ADR-0002); SQLite WAL is fine for a single-user workbench and small teams; the persistence port allows Postgres when a shared deployment is needed. |

## Missing requirements (added)

1. **Run identity across backends**: `RunRef(backend, run_id)` — the same id can exist in two workspaces.
2. **Replicate groups** (seeds) as a first-class concept (see S1).
3. **Metric naming conventions** for splits and dimensions, plus a mapping layer for existing names.
4. **Baseline selection policy** ("what changed?" needs a default baseline): latest completed `role=baseline` run in the same experiment, overridable.
5. **Data versioning boundary**: dataset version is compared, but dataset *content* drift is out of scope until dataset adapters exist.
6. **Offline / air-gapped operation**: the local connector + offline interpreter make the product useful without cloud access.
7. **Audit log** for AI tool calls and downloads.
8. **Schema versioning + migrations** for persisted findings/workspaces.

## UX risks

* A dense tool with 15 screens risks being a maze. The IA (architecture §19) centres on three
  loops: *find → inspect → compare/diagnose*; everything else is reachable from those via the
  command palette.
* AI output must never visually look like measurement. Every AI claim is badged `AI` + its type
  and verification state; deterministic findings are badged `COMPUTED/OBSERVED/INFERRED`.

## Security risks

* Azure credentials: use `DefaultAzureCredential` (managed identity / az login); never store keys.
* LLM keys: referenced by env var name; redacted from logs by value and by pattern.
* Artifact names from remote systems are untrusted → `safe_join` rejects traversal, absolute paths, drive letters, NUL.
* Pickled checkpoints are code: the product never unpickles artifacts. Checkpoint metadata inspection (Phase 2b) will use `safetensors` or `torch.load(weights_only=True)` in a subprocess sandbox.
