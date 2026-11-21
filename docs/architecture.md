# Model Forensic — Architecture & Product Design

Status: Phase 0 complete; Phase 1–3 vertical slice implemented (backend, API, CLI, web UI). Sections are numbered to match the deliverables list of the master prompt (§38).
Read `requirements-critique.md` first: several design decisions below exist because of it.

---

## 1. Executive product vision

Model Forensic turns "something is wrong with this model" into **"here is the evidence, here are
the most likely causes, here are the competing hypotheses, and here is the cheapest experiment that
distinguishes them"**, in minutes instead of a day of clicking through Azure ML Studio.

It is a forensic workbench, not a dashboard. Its core unit of output is a **Finding** — a
structured, provenance-tracked claim with evidence, hypotheses and recommended experiments — not a
chart. Charts are evidence attached to findings.

The product rests on one architectural rule (master prompt §37):

```
DATA RETRIEVAL  →  COMPUTATION  →  INTERPRETATION  →  RECOMMENDATION
 "what happened"   "what we measure"  "what it suggests"  "what to do next"
  connectors        analysis plugins   rules + AI            experiment proposals
  (OBSERVED)        (COMPUTED)         (INFERRED/HYPOTHESIZED/AI_GENERATED)  (RECOMMENDED)
```

Every value shown to the user carries one of those epistemic labels.

## 2. User personas

| Persona | Context | Primary need | Success metric |
|---|---|---|---|
| **Perception research engineer** ("Sam") | Runs 10–50 training jobs/week on a sparse multimodal 3D detector | "Why is my new idea worse than baseline?" | Time from job finished → defensible explanation |
| **Tech lead / reviewer** ("Alex") | Reviews ablations before they go into a paper or a release | "Is this improvement real or seed noise? Was it a controlled comparison?" | Fewer wrong conclusions shipped |
| **ML platform engineer** ("Robin") | Owns the training infra | "Which failures are infra (OOM, preemption) vs. user error vs. numerics?" | Failure triage time |
| **New team member** ("Kai") | Onboarding onto an existing experiment history | "What has been tried, what worked, what is the baseline?" | Time to first useful experiment |

## 3. Core user journeys

1. **Regression triage** — Sam opens the candidate run (`Ctrl+K → open cand-radar-8f`). The run page
   auto-selects the experiment's baseline and shows *What changed*: 4 config changes, regressions
   ranked by severity with replicate-based z-scores, and "recall regression concentrated in
   range=60-inf". One click on *Analyze* adds config-attribution hypotheses and three proposed
   single-variable ablations ranked by information gain per GPU-hour. *Ask AI → Why is this worse
   than baseline?* produces a cited, grounded narrative. *Generate report* exports Markdown/PDF.
2. **Failure triage** — Robin searches `failed training jobs from last week`, opens the NaN run;
   training-dynamics shows *Non-finite loss at step 4700* and *all loss terms grow together*
   (global instability, not a single head) with the log tail fenced as untrusted context.
3. **Claim review** — Alex opens a study with baseline (3 seeds) and candidate, runs the
   *skeptical reviewer* persona, sees which claims are verified and which are threshold-only
   ("no replicates — significance unknown").
4. **Search & rediscover** — Kai searches `cfg:temporal.num_frames=8 NDS>0.6` or
   `show runs using 8-frame temporal windows` and sees the DSL the NL query was rewritten into.
5. **Return days later** — a Forensic Workspace restores runs, baseline, findings, notes, AI
   conversations and reports.

## 4. Architecture diagram

```
┌──────────────────────────── Presentation ───────────────────────────────┐
│  Web UI (React/TS, Vite)            CLI `mf` (typer)      HTTP API (FastAPI)│
└───────────────┬───────────────────────────┬─────────────────────────────┘
                │                           │
┌───────────────▼────────── Application ────▼─────────────────────────────┐
│ ForensicService (search, inspect, compare, analyze, download) · Studies │
│ Investigator (AI orchestration)   ToolBox (bounded, permissioned tools)  │
│ Ports: RunSource · SearchIndex · FindingStore · WorkspaceStore · LLMProvider │
└──────┬──────────────────┬───────────────────────────┬───────────────────┘
       │                  │                           │
┌──────▼───── Domain ─────▼───┐   ┌──── Analysis engine ───────────────────┐
│ Run, MetricKey/Series,      │   │ AnalysisPlugin contract + registry       │
│ Artifact, ConfigDiff,       │◄──┤ signal.py (robust primitives)            │
│ Comparison, Finding,        │   │ comparison.py (noise-aware deltas)        │
│ Evidence, Hypothesis,       │   │ config_diff.py (semantic diff)            │
│ ExperimentProposal,         │   │ plugins: training_dynamics, loss,          │
│ Provenance, Workspace       │   │   regression, config_attribution, sparse   │
└─────────────────────────────┘   └───────────────────────────────────────┘
       ▲  (domain imports nothing below it)
┌──────┴─────────────────── Infrastructure (adapters) ────────────────────┐
│ connectors/azureml (SDK v2 + MLflow) · connectors/local · demo generator │
│ search/sqlite_index (FTS5 trigram) · artifacts/store (resumable, CAS)    │
│ persistence/sqlite_store · llm/openai_compatible · observability         │
└──────────────────────────────────────────────────────────────────────────┘
```

Dependency rule: arrows point inward. `domain` imports nothing from the project; `analysis`
imports only `domain`; `application` imports `domain` + `analysis` + port protocols;
infrastructure implements ports. `container.py` is the only composition root.

## 5. Component architecture

| Component | Responsibility | Interface | Invariants | Errors |
|---|---|---|---|---|
| `domain` | Vendor-neutral types | pydantic frozen models | immutable; `schema_version` on persisted roots | `ValueError` on invalid construction |
| `analysis.signal` | Robust 1-D primitives | pure functions | no I/O; tolerate NaN/short series | none |
| `analysis.comparison` | Noise-aware deltas | `compare()`, `metric_delta()` | verdict needs direction; replicate z only with ≥2 replicates & non-zero spread | none |
| `analysis.config_diff` | Semantic diff | `ConfigDiffer.diff()` | `"1e-4" == 0.0001`; noise paths separated | none |
| `analysis.plugins.*` | Findings | `AnalysisPlugin.run(ctx, params)` | deterministic; no I/O; ids stable for same inputs | caught per-plugin → `skipped_reason` |
| `application.service` | Orchestration, caching | `ForensicService` | analyses receive materialized data only | `ForensicError` subclasses |
| `infrastructure.connectors` | Backend → domain mapping | `RunSource` port | read-only; never leak SDK objects | `NotFoundError`, `BackendUnavailableError` |
| `infrastructure.search` | Fast search | `SearchIndex` port | favorites survive re-sync | — |
| `infrastructure.artifacts` | Downloads | `ArtifactStore.fetch()` | path-safe; hash-verified; resumable | `IntegrityError`, `InsufficientDiskError`, `CancelledError` |
| `ai.context` | Bounded evidence bundle | `ContextBuilder` | untrusted text fenced with nonce; budgeted | — |
| `ai.grounding` | Verify AI claims | `ground()` | no claim presented as fact without citation & numeric match | — |
| `ai.investigator` | AI loop | `Investigator.investigate()` | deterministic analyses first; ≤ `max_steps` tool rounds | provider errors propagate as `ForensicError` |
| `reports` | Markdown/HTML | `build_report()` | generated only from structured objects | — |

## 6. Domain model

```
RunRef(backend, run_id) ─┐
Run ── git:GitInfo, compute:ComputeInfo, datasets[DatasetReference], models[ModelReference],
       parent/children: RunRef, failure: FailureInfo, tags, params, summary_metrics
MetricKey(name, split∈{train,val,test,unknown}, dims=((k,v),...))   e.g. val/recall[range=60-inf]
MetricSeries(key, steps, values, axis, timestamps, unit, source, provenance)
MetricDescriptor(name|glob, higher_is_better, unit, family, min_meaningful_rel_change)
Artifact(run, path, kind, size, sha256, etag) ; Checkpoint(artifact, step, epoch, is_best)
ConfigurationSnapshot(run, source, data) → ConfigDiff(baseline, candidate, changes[ConfigChange])
Comparison(baseline, candidate, deltas[MetricDelta], config_diff, baseline_replicates)
Finding(id, title, statement, category, severity, confidence, label,
        evidence[Evidence], hypotheses[Hypothesis], recommendations[ExperimentProposal],
        limitations, provenance: Provenance)
Study(id, title, goal, runs, baseline, hypotheses[StudyHypothesis(statement, status, evidence[EvidenceLink],
      proposed_change, reference_run, test_run, verdict)], notes, pinned_findings, investigations, timeline)
```

Design notes:

* **Dimensions in metric keys** are the single most important modelling decision: they make
  slice localization, modality/range/class breakdowns and hidden-regression detection generic.
* `Run.backend_extras` exists for display only (e.g. Studio URL); analysis code must not read it.
* `Evidence.data` carries small plot-as-data payloads, so the AI and the UI consume the same thing.

## 7. Interface definitions

Defined in `backend/src/model_forensic/application/ports.py` (authoritative). Summary:

```python
class RunSource(Protocol):
    name: str
    def list_runs(self, query: ListRunsQuery) -> Page: ...                  # cursor pagination
    def get_run(self, run_id: str) -> Run: ...
    def list_metric_keys(self, run_id: str) -> list[MetricKey]: ...
    def get_metric_series(self, run_id, keys=None) -> list[MetricSeries]: ...
    def get_configs(self, run_id) -> list[ConfigurationSnapshot]: ...
    def list_artifacts(self, run_id) -> list[Artifact]: ...
    def open_artifact(self, run_id, path, offset=0) -> Iterator[bytes]: ...  # resumable
    def read_log(self, run_id, name=None, tail_bytes=None) -> str: ...

class AnalysisPlugin(Protocol):
    name, version, title, description, category: ClassVar[str]
    requires: ClassVar[frozenset[Requirement]]     # series | baseline | config_diff
    limitations: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[AnalysisParams]]         # pydantic → JSON schema for the UI
    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult: ...

class LLMProvider(Protocol):
    model: str
    def complete(self, messages, tools=None, *, json_mode=False) -> LLMResponse: ...

SearchIndex, FindingStore, WorkspaceStore — see ports.py.
```

`tests/contract/test_run_source_contract.py` runs the same behavioural suite against every
`RunSource` (local + Azure with SDK fakes).

## 8. Repository structure

```
Model-Forensic-/
├── README.md                     quick start
├── docs/
│   ├── architecture.md           this file
│   ├── requirements-critique.md
│   ├── adr/                      architecture decision records
│   └── development.md            dev workflow, troubleshooting, contributing
├── backend/
│   ├── pyproject.toml            ruff, mypy --strict, pytest config
│   ├── src/model_forensic/
│   │   ├── domain/               pure types (no project imports)
│   │   ├── analysis/             computation: signal, comparison, config_diff, registry, plugins/
│   │   ├── application/          ports.py, service.py
│   │   ├── ai/                   context, grounding, prompts(+packs), tools, investigator, fake_llm
│   │   ├── infrastructure/       connectors/{azureml,local}, search, artifacts, persistence, llm, demo
│   │   ├── reports/              markdown/html
│   │   ├── observability/        telemetry
│   │   ├── api/                  FastAPI app
│   │   ├── cli/                  `mf`
│   │   ├── shared/               errors, logging(redaction), retry, paths, hashing
│   │   ├── settings.py           YAML+env config
│   │   └── container.py          composition root
│   └── tests/{unit,integration,contract,e2e}
├── web/                          React + TypeScript (strict) + Vite + TanStack Query + uPlot
└── model-forensic.example.yaml
```

The prompt's suggested `apps/ packages/` monorepo split was **not** adopted: with one Python
service and one web app, a two-folder layout is simpler; package boundaries are enforced by the
dependency rule inside the Python package instead.

## 9. Technology decision matrix

Scores 1–5 (higher is better) against the prompt's criteria.

| Concern | Choice | Alternatives considered | Reliability | Velocity | Perf | Ecosystem | Typing | ML fit | Why |
|---|---|---|---|---|---|---|---|---|---|
| Backend language | **Python 3.10+** | TS/Node, Go | 4 | 5 | 3 | 5 | 4 | 5 | Azure ML SDK, MLflow, numpy are Python-native; analysis code must be shareable with ML engineers. |
| Domain models | **pydantic v2 (frozen)** | dataclasses, attrs | 5 | 5 | 4 | 5 | 5 | 4 | Validation + JSON schema + API serialization from one definition. ADR-0003. |
| API | **FastAPI** | Flask, Django | 4 | 5 | 4 | 5 | 5 | 4 | Typed, OpenAPI for free, async-capable. |
| Numerics | **numpy** | pandas, polars | 5 | 4 | 5 | 5 | 3 | 5 | Minimal dependency for 1-D signals; polars/Parquet when columnar caching lands. |
| Search/index | **SQLite + FTS5 trigram** | Postgres, Elasticsearch, Tantivy | 5 | 5 | 4 | 4 | 3 | 3 | Zero-ops, in-process, millions of rows with indexes. ADR-0004. |
| Persistence | **SQLite JSON documents** | Postgres | 5 | 5 | 4 | 4 | 3 | 3 | Same file-level ops story; port allows Postgres for shared deployments. |
| CLI | **typer** | click, argparse | 4 | 5 | — | 4 | 4 | — | Type-annotated commands. |
| LLM | **OpenAI-compatible HTTP via httpx** | vendor SDKs, LangChain | 5 | 4 | 4 | 5 | 4 | 4 | One protocol covers OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM; no framework lock-in. ADR-0006. |
| Frontend | **React + TypeScript (strict) + Vite + TanStack Query/Table + uPlot** | Svelte, Angular, Streamlit | 4 | 4 | 5 | 5 | 5 | — | Largest ecosystem for dense data UIs; uPlot handles 1M-point series. Streamlit rejected: not a product UI. |
| Packaging | **hatchling, uv** | poetry | 4 | 5 | — | 4 | — | — | Fast, standard. |

Architecture style: **modular monolith** (ADR-0002). Seams (ports) allow extracting the sync
worker and analysis workers into separate processes later without touching the domain.

## 10. Azure ML integration architecture

* Metadata: `azure.ai.ml.MLClient.jobs.list/get` (SDK v2). Auth: `DefaultAzureCredential`
  (managed identity, `az login`, workload identity); interactive browser login is opt-in.
* Metrics/params/tags/artifacts: Azure ML's MLflow-compatible tracking server
  (`MlflowClient.get_run`, `get_metric_history`, `list_artifacts`, `download_artifacts`).
* **Mapping** lives only in `connectors/azureml/mapper.py` (pure, duck-typed, unit-tested with plain
  objects): status normalization, job kind, git info from `mlflow.source.git.*`/`azureml.git.*`
  properties, datasets from inputs (version parsed from `azureml:name:version`), failure
  categorization (oom / nan / timeout / user_error / infra).
* **Metric naming**: `train/`, `val/`/`eval/`, `test/` prefixes → `Split`; `[k=v,...]` suffix →
  dimensions. Teams with different conventions add a name-mapping rule (Phase 1b).
* **Resilience**: client-side token-bucket rate limiter, bounded exponential backoff with jitter on
  429/5xx/timeouts, `NotFoundError` mapping, per-call latency logging.
* **Sync**: background job pages through runs and upserts the local index; `updated_since`
  makes it incremental. Running runs are re-fetched on access; terminal runs are cached.
* **Pipelines**: children via `azureml.pipelinerunid`/`parent_job_name`; the UI shows lineage.
* **Status / validation plan**: the adapter is implemented against the public SDK surface and passes
  the shared contract suite with SDK fakes, but has **not been run against a live workspace** yet.
  Validation: (1) recorded-response tests from a real workspace (`vcrpy`), (2) a nightly smoke job
  in a sandbox workspace, (3) pagination using the SDK's continuation token instead of the
  current numeric skip.

## 11. Artifact download architecture

```
select(artifacts, profile) ──► ArtifactStore.fetch()
   │                               ├─ cache lookup (manifest.sqlite: backend,run,path → sha256,size)
   │                               ├─ disk-space guard (needed + reserve)
   │                               ├─ ThreadPool(workers): stream → <file>.part (append from size → resume)
   │                               │      ├─ retry with backoff (resume offset preserved)
   │                               │      └─ cancel Event checked per chunk
   │                               ├─ sha256 verify (vs backend hash if provided) + size check
   │                               ├─ content-addressed blob  blobs/ab/abcd…  (dedup)
   │                               ├─ hard-link into runs/<backend>/<run>/<path>  (safe_join)
   │                               └─ LRU retention to max_bytes
   └─ profiles: FAST_DEBUG (logs, metrics, config, small eval ≤50 MB/file),
                EVALUATION_ONLY, CHECKPOINT_ONLY, FULL_FORENSIC, CUSTOM (kinds, size cap, globs)
```

Not yet: byte-range resume for Azure (MLflow artifact API is whole-file; needs direct Blob
`download_blob(offset=…)` with a SAS from the datastore), checkpoint metadata inspection without
full download (safetensors header read via range request).

## 12. Analysis engine architecture

* **Contract**: plugins are classes with class-level metadata and a pydantic `Params` model
  (→ JSON schema → auto-generated UI forms). `run()` receives an `AnalysisContext` with fully
  materialized `RunData` for target, baseline and baseline replicates, the `ConfigDiff`, and the
  metric registry. No I/O inside plugins.
* **Determinism**: finding/evidence ids are hashes of (plugin, version, params hash, runs,
  content) → re-running yields identical ids, which makes findings diffable over time.
* **Isolation**: a plugin exception becomes `skipped_reason`, never a failed request.
* **Registry**: built-ins + `model_forensic.analyses` entry points for third-party packs.
* **Implemented packs**: `training_dynamics` (NaN/Inf, divergence, spikes [robust MAD z +
  relative jump], oscillation, plateau with LR context, overfitting with checkpoint
  recommendation), `loss_forensics` (shares, dominant/dead/growing/stalled terms,
  loss-vs-eval decoupling), `regression` (replicate-aware deltas, severity, dimensional
  localization, hidden slice regressions), `config_attribution` (ranked hypotheses + ablations),
  `sparse_detection` (positive query ratio, duplicates, confidence collapse).
* **Architecture packs** (need the instrumentation kit, `instrument/`): `module_health` (per-module update
  ratio ‖Δθ‖/‖θ‖, gradient norm, dead gradients → dead / still / starved / unstable / stalled branches),
  `modality_ablation` (evaluation with a branch zeroed → contribution overall and per slice, change vs baseline),
  `feature_health` (per module and per FPN scale: collapsed maps, scale imbalance, growth, non-finite),
  `decoder_layers` (per-layer losses → saturation, later layers worse, still improving). Metrics with an
  `ablation` dimension are excluded from ordinary comparisons.
  `decoder_layers` also groups per branch (`branch=dn|aux2d`) and checks denoising health (DN task trivial: DN loss
  far below the matched loss; DN branch not learning).
* **Head packs** (`analysis/plugins/heads.py`, kit metrics): `uncertainty_health` (calibration ratio, interval
  coverage, error ranking, clamp saturation per box dimension and range; loss attenuation = predicted scale grows
  while the error does not fall), `temporal_memory` (loss/metrics by sequence position since memory reset; share and
  positive rate of propagated vs new queries), `loss_interactions` (per-term gradient share vs loss-value share on a
  shared module; conflicting pairs by gradient cosine, with decoupling proposals).
* **Planned packs (Phase 4)**: reliability diagrams from per-sample eval files, temporal by occlusion, data
  distribution, evaluation breakdown by velocity/size/occlusion.

## 12b. Studies and the instrumentation kit

**Study** (`domain/study.py`, `application/study.py`): the record of investigating one experiment or a small
group. All edits are small server-side operations applied under a lock to the latest stored study (no lost
updates between tabs), each appending a timeline event. `verify(hypothesis)` compares the hypothesis' test run
with its reference run (or the study baseline) and returns a suggested verdict that states the significance basis
and warns when more than one config change separates the runs; the status change stays a human decision.
API: `/api/v1/studies…`; CLI: `mf study list|new|hypothesis|verify|export`.

**Instrumentation kit** (`instrument/`, package `model_forensic_instrument`, no required dependencies): `Forensic`
(gradient/update monitor per parameter group, forward hooks for feature statistics per scale, loss-dict
normalization incl. MMDet `dK.loss_*` and suffix-indexed layers), `ablation_eval` / `zero_output` (evaluation
with modules zeroed), `log_slices` / `evaluate_by_slices` / `nuscenes_breakdown`, sinks for MLflow (Azure ML),
JSONL (local run layout) and callables, and an MMEngine `ForensicHook` that wraps `optim_wrapper.step` because
MMEngine runs backward/step/zero-grad inside `train_step`. MLflow rejects `[ = ,` in metric names, so the MLflow
sink encodes `val/recall[range=60-inf]` as `val/recall__range_60-inf`; `MetricKey.parse` decodes both.
Tested with real PyTorch and a real MMEngine 0.10 Runner.

## 13. AI / BYOK architecture

```
Investigator.investigate(run, question|prompt_id, baseline, persona)
  1. svc.analyze(...)                         deterministic findings (no LLM)
  2. ContextBuilder → evidence E1..En         runs, deltas, config changes, findings+evidence,
                                              fenced untrusted text, token budget w/ priority drop
  3. LLM loop (≤ max_steps):                  tool calls → ToolBox (permissioned) → result
                                              registered as new evidence E(n+1)
  4. final JSON {summary, insufficient_evidence, claims[type, text, evidence, confidence]}
  5. ground(): citations exist, FACT/INFERENCE cite ≥1 id, numbers match cited evidence
  6. persist Investigation (prompt id@version, model, context hash, tool log, usage)
```

* Provider: OpenAI-compatible chat completions; configurable endpoint, model, key env var,
  temperature, max tokens, reasoning effort, context limit, timeout, retry policy, Azure OpenAI
  mode (`api-version`, `api-key` header). Works with vLLM, Ollama, LiteLLM, other compatible servers.
* Personas: analyst, skeptical reviewer, CVPR reviewer, AD perception engineer, disprover.
* Offline mode: `OfflineHeuristicLLM` (labelled "offline-heuristic (no LLM)") converts top findings
  into grounded claims — keeps the pipeline testable and the product useful without a key.

## 14. Prompt system architecture

* YAML prompt packs (`ai/prompt_packs/*.yaml` + user dirs); fields: id, version, title, tags,
  requires, template. Syntax: `{{var}}`, optional sections `{{#var}}…{{/var}}`.
* Variables are whitelisted (`run_id, baseline_run_id, metric_name, dataset, model,
  temporal_window, modalities, loss_terms, evaluation_split, date_range, hypothesis`); unknown
  variables are rejected at load time.
* **Evidence requests**: a template's `include:` block (logs: errors/tail, metric-key globs → curve
  digests, config-path globs → side-by-side values, per-slice metrics) is added to the deterministic
  context. This lets small local models without tool calling answer from the right evidence.
  The context builder trims to the token budget in a fixed order (low-priority items, descriptions,
  log tail, curve digests, config lines) and states what was dropped.
* **Multi-run**: investigations accept up to 8 extra runs; each contributes its metadata, headline metrics
  and its meaningful config differences from the reference run. `{{run_ids}}` lists them for templates.
* **Built-in packs** (105 templates, incl. `architecture` over the kit's metrics): core, study_run, compare, camera, radar, lidar_fusion, temporal, heads,
  detection_head, training, logs_infra, data, review — catalogued in `docs/prompt-catalog.md`
  (generated; a test keeps it in sync).
* **Versioning**: edits create a new version; existing versions are immutable; investigations
  record `prompt_id@version`. Export to YAML. Tags drive the UI's prompt picker.

## 15. Security architecture

| Threat | Control |
|---|---|
| Credential leakage | Azure: `DefaultAzureCredential`, no stored secrets. LLM key: env var reference only; registered with the log redactor; pattern redaction (bearer, `api_key=`, `sk-…`). |
| Over-privilege | Read-only by default (`settings.read_only`); connectors are read-only by contract; AI tools default to READ; EXPENSIVE requires confirmation; MUTATING disabled. |
| Path traversal from artifact names | `shared.paths.safe_join` (rejects `..`, absolute, drive letters, NUL; resolved path must stay under root). Tested. |
| Malicious artifacts | Never unpickle; never execute; checkpoint inspection planned via `safetensors`/`weights_only` in a sandboxed subprocess. |
| Prompt injection via logs/configs | Nonce-fenced untrusted blocks with fence-token neutralization; system rule; tool outputs labelled `untrusted_*`; injection cannot escalate (no mutating tools; expensive tools need user confirmation). |
| Hallucination presented as fact | Grounding checker; UNVERIFIED labelling; "Insufficient evidence." default. |
| Audit | `audit` table: AI tool calls, downloads (actor, action, detail, timestamp). |
| Web | CORS restricted to the dev UI origin; API binds to 127.0.0.1 by default. Multi-user deployment adds Entra ID (OIDC) auth in front (Phase 7). |

## 16. Observability architecture

* Structured JSON logs (`shared/logging.py`) with redaction.
* In-process telemetry (`observability/telemetry.py`): counters + latency (p50/p95) for HTTP
  routes, backend calls (`backend.get_run`, `backend.get_metric_series`), sync, search, compare,
  each analysis, downloads (bytes, cache hits), LLM latency/tokens (logged per call).
* `/api/v1/diagnostics`: telemetry snapshot, index stats, artifact cache size, recent audit log.
* Upgrade path: wrap `Telemetry` with an OpenTelemetry exporter; no call sites change.

## 17. Data model (persistence)

| Store | Table(s) | Notes |
|---|---|---|
| `index.sqlite` | `runs` (denormalized columns + JSON doc), `run_tags`, `run_params` (flattened config), `run_metrics` (summary scalars), `runs_fts` (FTS5 trigram) | indexes on created_at, status, experiment, user, parent, (tag,value), (path,value), (metric,value) |
| `forensic.sqlite` | `docs(kind,id,updated_at,body)`, `doc_runs(kind,id,run)`, `audit` | kinds: finding, study, investigation, report; `schema_version` + migration hooks |
| `artifacts/manifest.sqlite` | `files(backend,run_id,path,sha256,size,fetched_at,accessed_at)` | blobs content-addressed on disk |

All persisted domain roots carry `schema_version`; `DocumentStore.get_raw` applies registered
migrations on read.

## 18. API design

REST, JSON, `/api/v1`. Errors: `{"error": {"code", "message", "details"}}` with stable codes
(`not_found` 404, `invalid_request` 400, `permission_denied` 403, `confirmation_required` 409,
`backend_unavailable` 503, `insufficient_disk` 507).

| Method & path | Purpose |
|---|---|
| `GET /health` | version, backends, LLM, read-only flag |
| `POST /sync` → job | background index sync |
| `GET/DELETE /jobs/{id}` | job status / cancel |
| `GET /runs?q=&limit=&offset=` | search (DSL or natural language; `notes` shows interpretation) |
| `GET /suggest?prefix=&field=` | autocomplete (name, experiment, user, branch, tag, metric, param) |
| `GET /runs/{run}?baseline=` | forensic overview incl. auto-baseline and "what changed" |
| `PUT /runs/{run}/favorite` | favorites |
| `GET /runs/{run}/metrics?keys=&max_points=` | downsampled series |
| `GET /runs/{run}/config?flat=` | config snapshots |
| `GET /runs/{run}/logs`, `/logs/search?pattern=` | logs (tail), regex search |
| `POST /runs/{run}/download?profile=` → job | artifact download |
| `GET /compare?baseline=&candidate=` | comparison |
| `GET /analyses` | plugin catalogue incl. params JSON schema |
| `POST /analyze` | run analyses → findings + plots-as-data |
| `GET /findings/{id}` | finding with provenance |
| `GET /prompts` | prompt library |
| `POST /investigate` | AI investigation (grounded) |
| `POST /reports` | Markdown or HTML (print-to-PDF) report |
| `GET/POST /studies`, `GET/PATCH/DELETE /studies/{id}`, `…/runs`, `…/notes`, `…/pins`, `…/hypotheses[/{hid}[/evidence|/verify]]`, `…/evidence`, `…/export` | studies |
| `GET /backends`, `GET /run-info`, `GET /metric-keys` | workspace selector, multi-run views |
| `GET /diagnostics` | internal diagnostics page data |

## 19. UI information architecture

```
Global: top bar [Azure ML workspace selector] [global search / Ctrl+K palette] [job tray] [settings]
Left rail: Home · Studies · Runs · Progression · Compare · AI · Library(Analyses, Prompts) · Artifacts · Reports · Settings

Home / Command Center   recent runs, recent failures, favorites, findings feed
Run Explorer            virtualized table; facet sidebar (status, experiment, user, compute, tags);
                        query bar with DSL chips + NL interpretation; multi-select → Compare
Run Details             header (status, git, compute, duration) · tabs:
                          Overview(What changed vs baseline ▾) · Metrics · Loss · Evaluation ·
                          Config · Logs · Artifacts · Lineage · Findings · AI
Compare Runs            N-way metric table w/ z-scores & verdicts · semantic config diff ·
                        overlaid curves (synced axes) · slice heatmap (dimension × run)
Metrics Explorer        multi-run, multi-metric; smoothing, log-scale, markers (checkpoint, baseline, events)
Loss / Multimodal / Temporal / Dataset Forensics   pack-specific views over findings + plots-as-data
AI Investigation        question/prompt picker · persona · claims list (type badge, ✓/✗ verified,
                        evidence chips → open source) · tool-call timeline
Analysis Library        catalogue, params form from JSON schema, run on selection
Prompt Library          versions, diff between versions, tags, export
Artifact Manager        per-run inventory, profiles, job progress, cache usage/retention
Reports                 generated reports, export MD/PDF
Settings / Providers    backends, LLM endpoint/model/key-env, budgets, read-only toggle; Diagnostics
```

## 20. UX design principles

Dense but readable (tabular numerals, 13px base, 4-px grid); keyboard first (palette, `g r` go
runs, `c` compare, `a` analyze, `?` help); persistent context (selected baseline and workspace
follow you across screens); progressive disclosure (finding → evidence → source); epistemic
honesty in the visual language (COMPUTED/OBSERVED neutral, HYPOTHESIZED dashed outline, AI violet
badge, UNVERIFIED amber); optimistic, non-blocking loading with skeletons and a job tray; empty
states that teach (`Try: status:failed since:7d`); errors that say what to do (codes mapped to
remedies); dark mode default with light theme.

## 21. Performance architecture

| Concern | Approach |
|---|---|
| Thousands–millions of runs | local index; all filters indexed; FTS5 trigram for fuzzy; `ORDER BY created_at` index; pagination |
| Slow Azure | background sync; UI reads index; terminal runs cached; running runs refreshed on access |
| Large metric histories | per-run LRU cache; API `max_points` downsampling; Phase 3: Parquet cache + LTTB downsampling |
| Multi-GB artifacts | profiles, resumable, parallel, dedup, retention |
| UI | virtualized tables, uPlot canvas charts, TanStack Query caching, streaming job progress |
| Analysis | numpy vectorized; plugins independent → parallelizable per plugin/run |

Measured on the demo index: search 0.6–1.5 ms; compare + all analyses on a 3-seed baseline < 1 s.

## 22. Testing strategy

| Level | Location | What | Count (now) |
|---|---|---|---|
| Unit | `tests/unit` | domain, signal (incl. property-based via hypothesis), config diff, each analysis plugin incl. false-positive guards, query parser, grounding, prompts, retry, redaction, path safety, Azure mapper | 49 |
| Integration | `tests/integration` | search index, artifact store (resume, dedup, integrity, cancel, traversal, retention), LLM provider (httpx MockTransport: auth, Azure mode, 429 retry, 401 no-retry), persistence | 18 |
| Contract | `tests/contract` | identical `RunSource` suite for local and Azure (SDK fakes) | 12 |
| E2E | `tests/e2e` | API: search → inspect → compare → analyze → investigate → report; error mapping; tool permissions | 4 |

Fixtures are deterministic synthetic runs with known ground truth (`infrastructure/demo/synthetic.py`):
every built-in analysis has a scenario it must detect and a clean scenario it must not flag.
CI: ruff, mypy --strict, pytest (see `.github/workflows/ci.yml`).

## 23. Deployment strategy

1. **Local workbench (now)**: `pip install -e backend && mf demo && mf serve`. Data in `.mf/`.
2. **Team server (Phase 7)**: one container (API + static UI) behind Entra ID OIDC; managed identity
   for Azure ML; Postgres for index/docs; Azure Files or local SSD for the artifact cache.
3. **Scale-out (only if needed)**: extract sync and analysis workers behind a queue — the ports
   already isolate them.

## 24. Development workflow

`uv venv && uv pip install -e "backend[dev]"`; `ruff format && ruff check && mypy src && pytest`;
trunk-based with short-lived branches; every new analysis needs a positive and a negative
synthetic fixture; every new connector must pass the contract suite. See `docs/development.md`.

## 25. ADRs

See `docs/adr/`: 0001 layered/hexagonal architecture · 0002 modular monolith · 0003 pydantic frozen
domain models · 0004 SQLite + FTS5 index · 0005 dimensions in metric keys · 0006 OpenAI-compatible
BYOK + grounding · 0007 replicate-aware significance · 0008 deterministic-first AI.

## 26. Phased implementation roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Architecture, ADRs, domain, ports, tech decisions | ✅ |
| 1 | Discovery, search (DSL+NL), run details, metrics, logs, config | ✅ backend/API/CLI/UI; Azure adapter needs live validation |
| 2 | Artifact download + caching, comparison | ✅ (Azure byte-range resume pending) |
| 3 | Metric viz + analysis engine | ✅ engine + 5 packs; interactive charts in UI |
| 4 | Loss ✅, uncertainty, sparse ✅(basic), multimodal, temporal packs | partial |
| 5 | BYOK AI investigation | ✅ with grounding + offline mode |
| 6 | Prompt library ✅ + agentic tools ✅ | ✅ |
| 7 | Studies ✅ (hypotheses, verification, journal, export) + reports ✅ (MD/HTML, print-to-PDF in UI) | ✅ |
| 8 | Lineage graph, experiment recommendation engine v2, advanced viz | not started |

## 27. MVP definition

An ML engineer with an Azure ML workspace can, from the web UI: sync and search runs; open a run
and see "what changed" vs an auto-selected baseline; compare two runs with replicate-aware
verdicts and slice localization; run the five analysis packs; ask grounded AI questions with BYOK;
export a report. **Remaining for MVP**: live Azure validation.

## 28. Post-MVP roadmap

Uncertainty/modality/temporal/data packs; per-sample evaluation ingestion (enables bootstrap CIs
and failure galleries); Parquet metric cache; checkpoint inspection; lineage graph; MLflow / W&B /
ClearML connectors (MLflow is nearly free: Azure already uses it); team deployment with auth;
leaderboards and benchmark tracking; run clustering and similarity search; cost-per-experiment.

## 29. Example forensic analyses (from the demo data, reproducible with `mf demo`)

```
$ mf analyze cand-radar-8f --baseline baseline-cl4f-s0
- regression: 9 finding(s)
  [CRIT] Regression in val/AMOTA                   val/AMOTA: 0.4842 → 0.421 (-13.1%), z=-13.2 vs 3 baseline seeds
  [HIGH] Regression in val/mAP (concentrated in range=60-inf)
         • mAP by range: 0-30 +0.1%, 30-60 -0.3%, 60-inf -14.1%
  [MED ] Regression in val/recall (concentrated in range=60-inf)
         • recall by range: 0-30 -0.3%, 30-60 -0.7%, 60-inf -16.6%
- config_attribution: [MED] Candidate causes from configuration changes (hypothesized, conf=low)
         ? model.fusion.modalities: ['camera','lidar'] → ['camera','lidar','radar'] (modality)
         ? temporal.memory_queue 600 → 1200 ; temporal.num_frames 4 → 8 ; optimizer.lr 2e-4 → 4e-4
         → [1] Ablate model.fusion.modalities  [2] Ablate temporal.memory_queue  [3] ...
$ mf analyze lr-sweep-1e3
  [CRIT] Non-finite loss (NaN/Inf) at step 4700 ; [CRIT] Training diverged (13.6x minimum)
  [HIGH] All loss terms grow together → global optimizer instability
$ mf analyze long-schedule-overfit
  [HIGH] Overfitting after best validation step (val loss +19.4% from min at step 7000)
         → Evaluate the checkpoint nearest the validation minimum
  [HIGH] Training loss improves while AMOTA, NDS, mAP worsen
```

## 30. Example AI conversation

```
$ mf ask cand-radar-8f --baseline baseline-cl4f-s0 --prompt why_worse --persona skeptical_reviewer
[offline-heuristic (no LLM)] 11 deterministic finding(s); top: Regression in val/AMOTA; ...
  ✓ FACT        Regression in val/AMOTA: val/AMOTA regressed by -13.1% relative to the baseline.  [E32, E31]
  ✓ FACT        Regression in val/AMOTP: ... +11.1% ...                                          [E34, E33]
  ✓ HYPOTHESIS  The change model.fusion.modalities: [...] → [..., 'radar'] contributes            [E59]
```

With a real model, an *invalid* answer looks like this and is shown as such, not hidden:

```
  ✗ FACT  NDS fell by 9.7% because radar adds noise at long range   [E1]
      ! number 9.7% not found in cited evidence
```

## 31. Example prompt templates

See `backend/src/model_forensic/ai/prompt_packs/core.yaml` (10 templates: explain_run,
why_worse, why_failed, largest_regression, training_dynamics, loss_decomposition,
modality_contribution, temporal_helping, skeptical_reviewer, next_experiment).

## 32. Example findings (JSON, abridged)

```json
{
  "id": "fd-3c1e9a0b27", "title": "Regression in val/recall (concentrated in range=60-inf)",
  "category": "regression", "severity": "medium", "confidence": "high", "label": "computed",
  "evidence": [
    {"id": "ev-…", "label": "computed", "statement": "val/recall: 0.6012 → 0.5794 (-3.6%), z=-10.4 vs 3 baseline seeds",
     "source": {"run": {"backend": "local", "run_id": "cand-radar-8f"}, "metric": "val/recall"}},
    {"id": "ev-…", "label": "computed", "statement": "recall by range: 0-30 -0.3%, 30-60 -0.7%, 60-inf -16.6%",
     "data": {"slices": {"0-30": -0.0026, "30-60": -0.0071, "60-inf": -0.1665}}}
  ],
  "provenance": {"analysis": "regression", "analysis_version": "1.0.0", "params_hash": "…",
                 "runs": [{"backend":"local","run_id":"baseline-cl4f-s0"}, {"backend":"local","run_id":"cand-radar-8f"}, "…replicates"]}
}
```

## 33. Example forensic report

`mf report cand-radar-8f --baseline baseline-cl4f-s0 -o report.md` produces: Executive Summary,
Problem Statement, Runs Investigated, Configuration Differences, Metric Comparison (with
significance basis), Training Dynamics, Loss Analysis, Evaluation Analysis, Configuration
Attribution, Multimodal/Temporal (scoped as not-yet-available), Hypotheses, Alternative
Explanations, Recommended Experiments (priority, controlled variable, measurement, acceptance,
cost, information gain), Confidence, Limitations, Appendix (provenance table, AI claims with
verification status). `-o report.html` produces a print-ready HTML for PDF.

## 34. Failure modes and mitigations

| Failure mode | Effect | Mitigation |
|---|---|---|
| Azure throttling / outage | sync stalls | rate limiter, bounded retry, index keeps serving stale-but-labelled data |
| Metric naming differs per team | analyses skip | `skipped_reason` explains what was expected; name-mapping rules; metric registry YAML |
| Single-seed comparison | noise flagged as regression | threshold basis + explicit limitation + MEDIUM confidence cap |
| Confounded config changes | false causal story | HYPOTHESIZED label, "confounded" text, ablation proposals |
| LLM hallucination | wrong conclusion | grounding checker, UNVERIFIED badges, insufficient-evidence default |
| Prompt injection in logs | AI manipulated | fencing + no privileged tools + confirmation |
| Huge checkpoint pulled accidentally | disk full / slow | profiles, disk guard, confirmation for AI-initiated downloads |
| Partial download / corruption | wrong analysis | `.part` + hash + size check |
| Plugin bug | broken page | per-plugin isolation |
| Schema evolution | old findings unreadable | `schema_version` + migrations |
| Heuristic thresholds wrong for a team | noisy findings | thresholds are plugin `Params` (overridable per request/workspace) and tested against negative fixtures |

## 35. Future extension points

`RunSource` (MLflow, W&B, ClearML, Kubernetes, local TensorBoard) · `AnalysisPlugin` via entry
points · metric registry YAML · prompt packs · `LLMProvider` (Anthropic-native, Bedrock) ·
`SearchIndex` (Postgres) · `FindingStore`/`WorkspaceStore` (Postgres) · telemetry exporter
(OpenTelemetry) · report renderers (PDF via WeasyPrint/Playwright) · dataset/model adapters ·
visualization modules once the UI exists.
