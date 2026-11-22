# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org); the HTTP API is versioned under
`/api/v1`, stored documents carry a schema version and are migrated on read.

## Unreleased

### Prefetch tasks: plan, then download; stop and resume later
- **Plan first**: every file of the run and its stages with its size, a pre-selection (profile or chosen files),
  what is reused (verified cache), what resumes from a partial download, what is left, an estimate from earlier
  throughput (none is guessed before a download was seen), free disk, and risks (unknown sizes, files over 1 GB, not
  enough space). Change the selection, then **Start**.
- **Stop · resume later** keeps finished files and partial downloads; **Resume** (also after a server restart —
  running tasks come back *stopped*) re-validates finished files and fetches only what is missing, continuing
  partial files from where they stopped. **Cancel** is not resumable and removes partial downloads. States:
  planned, running, stopping, stopped, completed, failed (resumable), cancelled.
- New **Prefetch** page (task list with progress, speed and time left; files with their state; history). Downloads
  run on the server and continue while you use other pages. *Bundle*, the Artifacts tab and the command palette
  now open a plan instead of downloading straight away. `/prefetch`, `/prefetch/plan`, `/prefetch/{id}/…`.
- The Runs-table wheel is now called **warm-up** (metadata only) — *prefetch* means artifact downloads.
- **Integrity fix**: cached files are checked on disk (size always, SHA-256 when planning or resuming, up to
  256 MB) — a truncated or modified file is downloaded again instead of reused; stored files are read-only.

### Paths: PROJECT_PATH and the Model Forensic folder
- Relative paths you give (`mf inspect outputs/_azure_artefacts`, local run folders, knowledge sources) resolve from
  `PROJECT_PATH` (your project root), wherever `mf` runs; previously from the current directory.
- Model Forensic's own files (`config/`, `.mf/`, `harness/`, `model-forensic.yaml`) resolve from the Model Forensic
  folder (found automatically, `MF_HOME` overrides) — starting `mf` from the project root no longer misses
  `config/azure.local.yaml`.
- `scripts/set-project-path.sh` writes `export PROJECT_PATH=…` to `~/.bashrc` and `~/.zshrc` (idempotent);
  `mf paths` shows both roots; `mf doctor` reports them; `mf inspect` says where a missing folder was looked for.

### Header: connect and check the LLM in one click
- The model badge has a status dot (green: answered, red: did not answer, grey: no LLM) and a plug button.
  Offline, the plug asks the local server (`127.0.0.1:8080/v1`, change with `local_llm_url` /
  `MF_LOCAL_LLM_URL`) which model it serves, sends one test chat and switches to it only if the reply comes back;
  connected, it checks that the model still answers. A toast says what happened and why.
- LLM settings changed in the UI (or by the plug) are kept in `<data_dir>/llm.json` (never a key) and restored after
  a restart — previously they were lost and the badge fell back to *offline-heuristic*.
- `POST /settings/llm/ping`, `POST /settings/llm/connect-local`; `/health` reports the last check.

### Runs: prefetch with a wheel, my experiments
- Every row of the Runs table has a **prefetch** button: a cloud when idle, a turning wheel while it loads the
  run's pipeline stages, configs, metric series and file list, a tick when the run is cached (click again to refresh
  from the backend), a red mark with the reason if it failed. *Prefetch* also works on a selection.
- Finished runs' metadata is kept **on disk** (`<data_dir>/meta`, gzip, 2 GB budget by default —
  `meta_cache_max_gb`, 0 = off), so a prefetched run opens fast even after a restart; running runs are never cached
  on disk. `POST /runs/{run}/prefetch`, `GET /runs/prefetched`.
- **My experiments**: `experiment:mine` lists every run (anyone's) in the experiments where you have runs; a quick
  filter button and an *Experiment* picker (your experiments marked ★, with run counts). `GET /experiments`.

### Experience: your knowledge, checked against the runs
- Knowledge → **Experience**: write what you believe in your own words, optionally with a test — *changing a
  setting moves a metric* or *a loss family is muted / trusted / done early / still learning / rising* — and a scope
  (a run search). Autocomplete offers the config keys, metrics and loss families seen in the scope.
- Each belief is checked deterministically: runs are paired so they differ in the setting with as little else as
  possible (confounded pairs count less, hopeless ones are skipped), effects smaller than the seed noise count as no
  effect, pipeline stages are not double-counted. A Beta posterior gives the probability it holds, with an interval;
  your confidence is only a weak prior and the evidence-only estimate is shown beside it.
- **Colour bubble**: dark green (well supported, enough clean evidence), green, yellow, red, grey (not testable /
  no usable comparison). *What Model Forensic thinks*: summary, counterexample, what would settle it, the evidence
  table, the history of verdicts. *Check* / *Check all* run in the background.
- The study book checks your beliefs on every comparison that changes their setting. ADR-0011.

### Scale: hundreds of studies, drafts you can resume anywhere
- Studies list from small summaries stored with each study (search, Open / Closed / All, *Load more*,
  `X-Total-Count`) instead of parsing every full study; older databases are backfilled once. SQLite runs in WAL mode
  (readers never block the writer on a team server). Investigations list without reading them all.
- **Drafts live on the server**: the *New study* form and the guided study autosave (debounced, saved when you
  leave), any number of drafts can coexist, each resumable by link; *Drafts* on the Studies page; drafts from the
  old browser storage are moved over once. Per user, pruned after 90 days.
- **Continue where you left off** on the Command center: drafts, open studies with their next step, jobs that did
  not finish (*Run again*), knowledge waiting for review. ADR-0010.

### Knowledge from many codebases, one inbox
- **Sources**: any number of knowledge / documentation folders — `MF_KNOWLEDGE_SOURCE_<NAME>=<folder>` (one per
  codebase) or `knowledge_sources:` in `model-forensic.yaml` (`${VAR}`, `~`, `codebase`, `trust`, `exclude`).
- **One inbox** for every coding agent (`MF_KNOWLEDGE_INBOX`, default the knowledge folder); `mf knowledge where`
  prints the instruction to put in each codebase's agent instructions; the harness brief says where to write.
- Entries from agent-written sources apply only once accepted; accepting or retiring writes an override into the inbox
  (source files are never modified). Each entry shows where it was read from.
- **Documentation search**: Markdown / reST / text from every source in a full-text index (sections by heading path,
  BM25, incremental re-index); Knowledge → *Documentation* (search + reader), *Sources* (status, re-index),
  `mf docs search|index`, `mf knowledge sources`. The harness brief lists related documentation. ADR-0009.

### Study book: baseline → comparisons → conclusions
- **Part 1 · The baseline**: how each loss family progressed (raw loss per 1 % of training, when half / 90 % of the
  drop happened, still learning / plateaued / rising); **uncertainty weighting** per term (effective weight over
  training, self-muted / down- / up-weighted, trend, gradient share — "trust is not share"); **the composed
  configuration against what the run did** (contradicting uncertainty switches, task weights vs the gradient share
  each group gets, schedule overrun / early stop, LR, decoder depth, submitted vs composed values).
- **Part 2**: one chapter per run against the baseline — config changes (effective config of the training stage),
  loss overlays, σ changes, divergence point and the config changes that fit it, per-class deltas, regressions.
- **Part 3 · Conclusions** collects every chapter's conclusions and the next steps; the cross-run forensic chapters
  move to an appendix. Team knowledge is applied throughout (set-aside metrics and classes are marked and not
  counted). Optional grounded AI narrative per chapter as before.
- Charts with many series get a wrapping legend; a disabled local-dataset fallback no longer counts as a dataset
  when knowledge is matched.

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
