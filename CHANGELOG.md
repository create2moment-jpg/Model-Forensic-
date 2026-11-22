# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org); the HTTP API is versioned under
`/api/v1`, stored documents carry a schema version and are migrated on read.

## Unreleased

### Knowledge block (independent of runs)
- What the team knows lives in `config/knowledge/*.local.yaml` (git-ignored): constraints, facts, setup labels,
  caveats and code references, each with *when it applies* (setup, dataset, stage, config values, tags) and *what it
  changes* (metrics or classes set aside, a caveat, a setup label). New entries are *proposed* until accepted.
- Applied per run **and per pipeline stage**: a manual-label finetune stage sets velocity/acceleration aside while its
  pretrain stage does not. Findings, the run overview, Check my run and Precision & recall show what was set aside
  and by which entry — nothing is hidden.
- Page *Knowledge*: accept / reject / retire / edit, import.

### Harness adaptation (learn from the training codebase)
- `mf harness brief --run <id> --task <task>` writes a brief (stages, config keys and values, loss and metric names as
  logged, knowledge already known) plus a task prompt for a coding agent working inside the training project. Tasks:
  setup profiles, loss composition, uncertainty weighting, label targets, metric definitions, checkpoint & validation.
- The agent answers with knowledge YAML (`harness/knowledge.schema.json`, citing `file:line`); `mf knowledge import`
  stores it as *proposed*. Also in the UI (*Knowledge → Learn from the codebase*) and the API.

### Azure path re-aligned with the v1 tool (line-by-line audit)
- Metric histories now use the **sampled-metric API by default**, exactly as the v1 tool did on the real workspace;
  the full-fidelity API is opt-in (`metric_fidelity: full` in `config/azure.local.yaml`) and falls back to sampled
  values when it yields nothing usable (previously it was tried first and unusable rows meant an empty series).
- Pipeline stage runs always use the parent's experiment name (as v1), also after a restart — no per-stage job
  lookup. Child-stage listing, run-history artifacts, `stage_output/` blobs, SAS downloads and stage naming were
  checked line by line against v1 and are identical.

### Fixes from the first test on a real workspace
- **Pipeline runs showed 0 metrics and only the parent's log files**: Azure job listings carry no child stages and
  a finished run was always served from the index. Stages are now resolved once per pipeline run (the same
  `get_child_by_experiment_name` path as v1), kept in the index across re-syncs, and a failure leaves the run usable
  with the reason recorded. All stage-output presets (configs, evaluation JSON, parquet, metrics.csv, everything)
  follow from this.
- **Local LLM**: scheme-less base URLs (`127.0.0.1:8080/v1`) are accepted; loopback servers never go through
  `HTTP(S)_PROXY`; servers without JSON mode or tool calling are detected and the request is retried without them;
  `<think>` blocks are stripped; context overflow gives an actionable message. *Test connection* reports chat / JSON
  mode / tools separately.
- **My runs**: `user:me` in search and a *Mine* quick filter; your names come from `me:` in
  `config/azure.local.yaml` (written by `mf init`, editable in Settings → Azure) or the signed-in `az` user; user
  matching is case-insensitive.
- **Studies**: the *New study* form and the guided wizard keep their draft when you leave the page; *Cancel and
  discard draft* / *Cancel*; a study can be **closed** and reopened (closed studies sort last) besides **Delete**.

### Compare 2–10 runs: where and why they diverge
- Compare takes up to ten runs (Runs table selection, Progression, *Add a run*); the first is the reference.
- Per metric: aligned curves, the **divergence point** (first step from which the gap stays outside the
  step-to-step noise, marked on the chart), the outcome (ahead / behind / gap closed / crossed / same), the
  learning-rate context at that point, NaN onset, and the config changes that fit the timing (hypotheses).
- Sparse validation metrics are compared evaluation by evaluation; one exploding run switches the chart to log scale.
- `POST /api/v1/compare/curves`, `POST /api/v1/compare/ask` (grounded; offline it proposes one-change re-runs).
- Fix: chart marker labels were right-aligned (inherited from uPlot's axis labels) and could be clipped.

## 1.0.0rc2

### Explain this chart
- *Explain* on every metric chart of a run: a deterministic reading (trend, best value and where, plateau, noise
  level, final value vs the baseline with a noise check), warnings (NaN/Inf, isolated spikes or dips, final
  checkpoint ≠ best, a curve that got worse), **what to check next**, and markers on the chart (best, plateau, spikes,
  first NaN, LR drops; labels never overlap). Charts with non-finite values carry a NaN/Inf badge.
- *Explain* on Progression charts across runs: ranking by final value (running runs listed, not ranked), spread vs
  the typical step-to-step noise ("their order is not meaningful"), NaN and worsening runs, which runs flatten first,
  which are still improving.
- *Ask AI about this chart / these runs*: answered from the chart's reading only, every claim cited; without an LLM,
  the rule-based checks.

## 1.0.0rc1 — release candidate

First release candidate. Read-only towards Azure ML; everything site-specific lives in git-ignored
`config/*.local.yaml`.

### For everyone
- **Command center** with an insight on the newest run, what needs attention, what is running, open studies; a
  5-step guided tour.
- **Check my run**: at most five plain-language cards (traffic light, meaning, why it matters, what to do), a
  glossary, Simple / Standard / Expert modes. Run overview opens with the same story strip.
- **Connection problems explain themselves**: *Test connection* (Settings) and `mf doctor --live` turn every failure
  into a cause with ordered fixes (15 causes: expired login, wrong tenant, missing role, corporate TLS proxy,
  DNS/VPN, throttling, …); `--bundle` writes a redacted support file. `AGENTS.md` guides coding agents / local LLMs.

### Studies
- Guided study wizard (5 question templates, pre-registration, confounders), a gate that blocks verdicts while
  measurement checks fail (P1/P2/P3/P6), and a decision record.
- Study book: cover, print / save as PDF, verdict up front, pitfall checks P1–P8, shared ruler, convergence with the
  plateau stated in epochs, what each stage changed **by metric family** (with the degenerate-velocity caveat),
  **budget reallocation** between training stages, a **capacity** verdict, failure localisation, loss ledger, next
  steps, evidence sources. Reproduces 12 of 15 conclusions of a hand-written study (docs/study-reproduction.md).
- Optional grounded AI narrative per chapter (`--ai`): only claims citing the chapter's evidence survive.

### Analyses and trust
- Deterministic analyses: training dynamics, loss forensics / ledger, task weighting, decoder layers, heads,
  regression with replicates, config attribution, evaluation localisation, sequence drill-down, stage deltas,
  metric coverage, study checks.
- **Calibration**: `mf calibrate` scores every detector on a labelled benchmark (docs/calibration.md); defaults keep
  ≥ 90 % precision and recall (enforced by a test). The benchmark found and fixed three detector flaws (P8 on a
  planned cosine decay, divergence on heavy-tailed noise, spikes on heavy-tailed noise). Thresholds are parameters
  overridable per team.
- Every finding explains *why this confidence* and *what would change it*; *Is this finding right?* feedback with a
  false-alarm report per analysis.

### Experts
- Notebook bridge `model_forensic.connect()` (pandas), local analysis plugins with hot reload, launch gate
  (`mf gate`), replicate noise floor and effect sizes (`mf noise`).
- AI copilot: ask about a finding, next experiment as a validated config diff (invented keys rejected),
  `mf ai-eval` harness.

### Team server
- Shared mode with sign-in (trusted proxy header or OIDC), identity on every study action, background sync, Teams
  alerts for failed runs, nightly study books, `mf backup`; Dockerfile (non-root, health check, corporate CA
  support) and docker-compose with oauth2-proxy.

### Stability and security
- Typed API errors, no stack traces (request id instead), `/api/v1/health/deep` and a Status panel.
- M4 chart downsampling (spikes and NaNs survive), 30 s cache for running runs, partial results when a pipeline stage
  fails. Load test: 2,000 runs + a 5M-point run within budget (search 11 ms, cached run page 6 ms).
- 13 Playwright user journeys and a small load test in CI.
- Untrusted input limits: YAML alias bombs and oversized configs refused, malformed configs skipped, path traversal
  rejected, CSP embedded in generated HTML, security headers on every response.
