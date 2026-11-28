# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org); the HTTP API is versioned under
`/api/v1`, stored documents carry a schema version and are migrated on read.

## Unreleased

### The logs in the verdict, the review and the tracks; jump to the line
- **Log problems join the run's observations** (new source `logs`): errors are problems, warnings notes — in the
  **Review** (labelled *logs*, right after the code checks), the **verdict** on the run page and Today (which counts
  them), the dossier and the ledger. A **failed run's verdict names the cause from the logs** (*From the logs: CUDA out
  of memory — “…” (std_log.txt:211)* and what usually fixes it) and its next step opens Log insights.
- **Continuous training**: a track's sentence adds the newest run's log errors.
- **Never a download for this**: the verdict and the review read the logs only when that is free — a local run, or an
  Azure run whose logs were read before (the board was opened); otherwise they stand without them.
- **file:line is a link**: on Log insights every problem, event and unexplained line opens the Logs view at that file
  and line, highlighted (by number when the whole file is shown, else by its text). The link is in the URL
  (`?tab=logs&log=…&line=…`), so it can be shared.

### Log insights: everything the logs say, on the board
- Run page → Files → **Log insights** (first in Files): **problems** (errors, warnings, CUDA OOM, NCCL, NaN / Inf,
  killed processes, dataloader deaths, tracebacks — counted, first and last occurrence with file:line, what usually
  fixes them), **facts** (PyTorch / CUDA versions, world size, dataset and version loaded, epochs, evaluation results),
  **events** (checkpoints saved) and **curves** (loss by step, data time, throughput, any value a pattern names), plus
  the **noteworthy lines no pattern explains** yet.
- **Patterns from three sources**: built in (the v1 tool's readers — GPUs detected, datasets loaded, batch / worker
  overrides — and common failure signatures), **from the training code** (new harness task and work order
  `log_patterns`: the coding agent reads the code's print / log calls and writes `<inbox>/log_patterns/<codebase>.yaml`;
  *Brief for your coding agent* copies the brief with the run's unexplained lines), and **from the local LLM**
  (*Propose patterns*: grounded — a pattern is kept only when its regex compiles and matches the line it cites,
  verbatim; without an LLM the proposals are written deterministically from the lines' `name=value` numbers). Everything
  not built in is **proposed until accepted**.
- **Kept up to date cheaply**: each log is read from where the last read stopped — a running run refreshes every 30 s
  and reads only the new bytes; a large remote log is read from its end the first time (and says so).
- `mf logs insights|propose|patterns|accept|reject`; `GET /runs/{run}/log-insights`,
  `POST /runs/{run}/log-patterns/propose`, `/log-patterns`. Demo: `cand-radar-8f` has a realistic driver log.

### Pipeline graph and sweep view
- **Pipeline graph** on a pipeline's Summary: the steps left to right, each in the column after the steps that feed
  it, an arrow per connection labelled with what flows (*model → checkpoint*), status and time per step; click a step
  to open it. From the pipeline's own definition (each step input bound to `${{parent.jobs.<step>.outputs.<out>}}`;
  `mapper.pipeline_edges`, `Run.edges`) — no extra request. Pipelines without connections keep the stage strip.
- **Sweep view** on a sweep's Summary (*Trials*): the best trial in one sentence with its hyper-parameters, parallel
  coordinates (one axis per varying hyper-parameter, log scale when values span > 20×, the primary metric last; the best
  trial on top; click a line to open the trial), the trials ranked in the goal's direction (stopped-early and failed
  ones last). Objective from the sweep job (`objective.primary_metric` / `goal`), trial hyper-parameters from the
  trials' run records (`hyperparameters`); trial values the listing lacks are loaded once. `GET /runs/{run}/sweep`.
- Demo: `radar-lr-sweep` (six trials over learning rate × weight decay, one stopped early); the finetune pipeline has
  its graph.

### Resources: how a run used its machines
- Run page → **Resources** (under Timeline): GPU and CPU utilisation, GPU and CPU memory, disk and network over time,
  per node and GPU, with what it says — **GPUs mostly idle** (and whether a saturated CPU / input pipeline is why),
  **GPU memory headroom** or **near out-of-memory**, **a node lagging** the others, **disk nearly full**. Start-up and
  shut-down (first / last 5 %) are left out of the checks.
- Sources: **Azure Monitor** (the workspace's metrics, split by node and GPU; read-only; needs *Monitoring Reader* or
  Reader on the workspace — when it is refused the page says exactly that), else the **system metrics the run logged
  itself** (MLflow `system/…`), or a local run's `system_metrics.jsonl`. A pipeline asks for a stage (each ran on its
  own machines).
- Cost: one metric-definitions read per workspace, then at most four metric reads per run (GPU, CPU, disk, network),
  at an interval that keeps each curve under ~1,500 points; a finished run's answer is kept. Nothing is read until the
  view is opened.
- `GET /runs/{run}/resources`; `application/resources.py` (checks), `azureml/monitor.py`, `domain/resources.py`.

### The run's details, as studio's job Overview
- Run page → Summary → **Details**: time (created, started, ended, queued, ran, compute time in node- and GPU-hours),
  where (submitted by, experiment, compute, nodes / GPUs, **environment with its version**, code), the **command with
  one argument per line** (copy), **inputs and outputs** (asset name, version, path or literal value, mode), tags and
  **every job property** with a filter — values of secret-looking keys (token, SAS, password, key …) are hidden.
- No extra request: the job record the listing already reads now keeps its properties and inputs / outputs
  (`Run.properties`, `Run.io`, `mapper.redact`); the run list leaves them out so it stays small.

### How long a run took — as studio shows it
- The Azure job resource often has no start / end (pipelines, jobs that never logged through MLflow), so durations
  showed "—". They now come from **run history** (where studio reads them): **one query per experiment** of a listed
  page (newest first, stopped as soon as every asked run is seen), in parallel, bounded to 20 experiments per page;
  finished runs are read once and cached; a run opened before any sync gets one read. A failure never breaks the
  listing — the times stay empty and `mf doctor --live` says so (new **times** check: how many finished jobs have their
  times, from the job or from run history, and which do not).
- Run header: **Ran 9 h 04 m · ended 21 h ago · queued 4 min** (labelled; exact times on hover); **Running for …**
  ticks while it runs; queue times under 30 s are left out. The Runs table counts a running run's time live.

### Continuous training: follow recurring runs
- Runs → **Continuous training**: pick the workspace (e.g. the continuous-training workspace) and an experiment from two
  pull-down menus and *Follow* — as many experiments, in as many workspaces, as you like. Each track's page shows the
  headline metrics over its runs with the **best so far**, every run **vs the previous finished run and vs the best**
  (noise-aware: a run within the metric's meaningful change of the best does not replace it), run and queue time,
  failures, the data version; *Latest vs best* opens the comparison. Missing metric values (Azure listings carry none)
  are loaded when the page opens and cached.
- Today: a *Continuous training* card — each followed track's latest run in one line (best so far / below the best /
  failed …); *On Today* per track.
- `mf track add|list|show|check|remove` (`check` exits 1 when the latest run failed or is below the best — for CI or
  cron), `/tracks` endpoints. Definitions live in the local document store (never in git, never in Azure).

### Accessibility, faster first load, in-between widths, Knowledge fix
- **Accessible (WCAG 2.1 AA) on the main pages**, checked by axe in the e2e run (`e2e/a11y.spec.ts`, dark and light):
  faint text, status pills and filled buttons now reach 4.5:1 contrast in every theme (`--text-3`, the daylight
  status colours, `--accent-strong`); every checkbox, switch, slider and picker has a name; tabs are a proper tab
  list — focusable, ←/→/Home/End move between them; the search box opens with Enter / Space.
- **Faster first load**: each page is loaded when first opened, and the libraries are files of their own (cached
  across UI updates) — Today loads ~380 kB of JavaScript instead of 900 kB.
- **In-between widths (900–1280 px)**: the top bar drops the breadcrumb, then the search label and the model's name
  (its status dot stays); page headers keep their buttons on one row.
- **Knowledge**: the harness's loss expectations in `<inbox>/expectations/` were read as knowledge files and shown as
  a parse error — that folder (and `docs/`) is no longer read as knowledge.
- Page titles match their place (Offline copies, Check a run, Ledger), without icons; narrow pages (Studies,
  Knowledge, Settings, …) start where the section bar starts instead of being centred.

### Across the runs on Compare; running runs live on Today; phone width
- **Compare with three or more runs** shows *Across the runs* on the page itself: each headline metric over the runs in
  the order picked (baseline first, dashed line at its value; runs labelled B, 1, 2, …), the best run marked with its
  change against the baseline, an arrow when every candidate is better or worse. From the indexed values (no history
  load); *Curves over steps* opens the same runs in Progression.
- **Today → Running now** reads each running run live: on track / worth a look / problem (the top problem named), its
  curve so far and how long it has run — from what is loaded (`cached_only`), read again every minute.
- **Phone width** (≤ 700 px): one column; the top bar keeps the selectors (narrowed) and search as an icon, the
  rest scrolls; page headers and Today's newest-run line wrap; no page is wider than the screen.

### Stages under their pipeline; recently opened; quieter tables
- **Runs and Today list a pipeline once.** Its stages unfold under it (*▸ 3 stages*, read from the index with
  `parent:<id>`), instead of filling the list beside it — which matters now that every stage is indexed. *Stages*
  (next to *Metric columns*) lists them as rows of their own again; remembered per browser. Search: `is:top`
  (pipelines and plain runs) and `is:stage` (stages only); `GET /runs?top=true`.
- **Recently opened.** The runs and comparisons you opened, newest first, in the view you left them (tab, baseline,
  stage): ⌘K with nothing typed lists them first (⌘K Enter goes back), Today has a *Recently opened* card (× forgets
  one). Per browser (`web/src/lib/recent.ts`).
- **A pipeline opens on the stage you last picked on it** (unless the link names one); *Last stage per metric* clears it.
- **Quieter tags.** Only `role=baseline` / `role=candidate` are badges; other tags are one line of small grey text.
- **Today:** the runs table shows the metric most of the listed runs have (from the server's ranked columns — no
  hard-coded name) and no longer clips on the right; *Running now* shows how long each run has been running; the
  shortcuts card reads the one shortcut list (it named retired ones) and opens the full sheet.
- On a narrow window the sidebar shows its icons (it showed the key hints instead).

### Pipeline stages are runs in the index
- The Azure job listing carries no pipeline stages, so a stage opened, compared or browsed on its own had only an id
  ("unknown"). The stages now come from the run-history child listing as runs of their own — name, status, start /
  end, failure, parent pipeline, with the pipeline's experiment, owner, code version and compute — and go into the
  index: during sync (up to `sync_stage_budget` = 50 pipelines per sync, new and changed ones first; known stages are
  kept, not fetched again) and whenever a pipeline is opened. A stage the job API does not return is answered from
  the child listing. `AzureMLRunSource.stage_runs`, `mapper.map_child`, `ForensicService._index_stages`.

### Compare: runs on and off in the charts
- Compare → Metrics and Plots: one chip per run ("In the charts") switches the baseline or a candidate on and off as a
  whole — every curve, value and table row of it — instead of hiding curves one by one. Each run keeps its colour;
  one run always stays; the choice is in the link (`?hide=`); *show all* brings everything back.

### Compare: a stage left out keeps its pipeline
- Unticking a stage of a pipeline in Compare keeps **that pipeline** in the picker ("2 of 3 stages · without …"),
  the left-out stage listed, unticked (and struck through) to tick back any time; all ticked is the plain pipeline
  again; at least one stage stays in. Written as `group:<pipeline>!<stage>` and expanded from the pipeline's own stage
  list — before, the stages became loose runs that showed as "unknown" with raw ids when the index did not know them.

### Open it from another machine
- On SSH, `mf serve` prints the tunnel command with your user and the machine's address filled in.
- `mf serve --remote` listens on every interface behind an **access token** (local mode has no sign-in otherwise):
  it prints the link (`…/?token=…`), the first visit turns it into an HttpOnly cookie and drops it from the URL,
  scripts send `Authorization: Bearer` or `X-MF-Token`; `/api/v1/health` stays open; `mf status` prints the link
  again (the run record is readable by you only); `--token` / `MF_ACCESS_TOKEN` fix the token.

### Redesign: the answer first
- **Verdict** on the run page, Today and Compare: one sentence, ≤ 3 headline numbers with trend lines against a
  baseline picked for you (with the reason), within-noise changes shown as noise, one next step — from the same
  analyses the Review shows. `GET /runs/{run}/verdict`, `GET /compare/verdict`, `mf verdict`.
- **Run page**: Summary · Metrics · Review · Files · Timeline (sub-views remembered; `?tab=` links unchanged);
  Compare + Ask (drawer) + `⋯`; a progress line instead of the loading panel; a local run is read at once.
- **Navigation in five places** (Today, Runs, Compare, Studies, Knowledge) with sections above the page, from one
  source (`web/src/lib/places.ts`); every route kept; Settings → Navigation → *Classic*. "Command center" is Today.
- **Polish**: undo instead of confirm dialogs; paste a run id / studio link / folder into ⌘K (`GET /resolve`); `?`
  shortcuts sheet; `j` / `k` in the runs table.
- **Delight**: watch a running run (notification when it ends or a problem appears); code references open in the
  editor; a comparison as Markdown for a PR with text sparklines (`GET /compare/summary.md`, `mf summary`).
- **Starred experiments** (`exp:starred`), **sync inside the app** (per-workspace runs, last sync, sync buttons,
  auto-sync of an empty workspace), **Appearance** (Midnight, Graphite, Ocean, Ember, Obsidian, Daylight; expressive
  touches; motion).
- **Logs**: every log of the run and its stages, the telling one first, ranged tail reads, download the whole file.
  **Stages** open on their own (no wait for the whole pipeline); stages load in parallel.
- **Local runs**: TensorBoard and evaluation files read in place; pick the folder by browsing or autocomplete.
- Fixes: concurrent downloads of one file, concurrent manifest writes, plot overlays over the 12-curve limit.
- Docs: README, guides, reference, FAQ and screenshots updated (`web/e2e/screenshots.spec.ts` regenerates them).

### Identity and LLM provider
- **Who is "me"**: an explicit list of names (exact, case-insensitive) in Settings — add / remove, or **Add to Me**
  next to a run's owner; saved to `defaults.me` in `config/azure.local.yaml` without touching anything else.
  `GET/PUT /me/names`.
- The local LLM address is **never assumed**: the plug asks once (the usual address is only a placeholder) and
  remembers it after the server answers; `local_llm_url` / `MF_LOCAL_LLM_URL` set it too.
- LLM requests are bounded (`llm.max_concurrency`, default 2); extra callers wait instead of flooding a local server.
- Final v4 report: `docs/master-prompt-v4.md`.

### Run timeline and code-diff evidence
- New run tab **Timeline**: run id, name, experiment, owner, status, **current stage**, duration, start / end,
  command, environment, compute; every pipeline stage with its role (setup / data / training / evaluation / …),
  status and duration from the backend, and sub-stages (queue / setup, training, evaluation, finalising) estimated
  from metric timestamps — drawn striped, clamped to the measured window, or marked *not placed* with the reason.
- Each stage lists its evidence (configs, metric series, files, logs, code diff). The **code diff** the job saved
  (`code/*repo_diff*.txt` by default; `artifacts.code_diff` in a project profile) is shown as raw evidence with file
  and line counts, from any stage that has one. `/runs/{run}/timeline`, `/runs/{run}/code-diff`.
- Fixes: `evaluate_pretrain` is an evaluation stage; local run folders are no longer kept in the metadata disk cache
  (a changed folder showed stale files after a restart).

### Storage and performance settings
- Every storage role can live on its own path — downloaded files (bulk), metadata cache, indexes and your work
  database, study books (fast) — set in **Settings → Storage & performance** or `config/storage.local.yaml`.
  Paths are validated before saving (exists or creatable, writable with a real test write, free space, SSD / HDD
  where the OS reports it, fit for the role) and apply at the next start.
- A guide to what is safe to delete (and what is your work); `docs/storage.md`.
- Parallel downloads: a recommendation from the CPUs this process may use (container quota aware) and the disk
  type, a **boost** with its resource implications, or a custom value — applied to the next download.

### Target setup protocol for a local coding agent
- `setup-agent/` — DISCOVER → DECLARE PRECONDITIONS → CHECK TARGET → REPORT GAP → ADAPT INTERFACE → VALIDATE → RUN:
  discovery, the precondition checklist, the compatibility / gap report format, where adaptation is allowed (only
  git-ignored configuration) and forbidden (the core), validation, several projects. Editing core logic for one
  project is explicitly prohibited; an interface gap is reported instead.
- `mf setup check [--live] [--json]` — every precondition classified *satisfied / missing / incompatible / unknown /
  optional* with the fix and the file allowed to change; checks that local files are git-ignored; `--live` runs the
  adapter contract against every project workspace; the report is kept in `config/setup/compatibility.local.json`.

### Projects: many target projects, one unchanged core
- **Project profiles** in `config/projects/<id>.local.yaml` (git-ignored; three committed examples: sensor fusion,
  lane detection, road attributes): workspaces or local run folders, experiment globs, metric-name mapping
  (aliases + regex rules, applied before the core's mapping), stage roles, artifact locations, terminology,
  documentation folders. Versioned (`contract_version: 1`); a workspace belongs to one project.
- A header **project selector** scopes every run list, search and experiment list; `GET /projects`,
  `GET /runs?project=`, `GET /experiments?project=`.
- The **adapter contract** is now library code (`projects/contract.py`): `mf projects check <id>` runs it against
  real workspaces (read-only); the contract test suite runs it on every built-in adapter and on a broken one.
- `docs/projects.md` (add a project from a clean checkout), ADR-0012.

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
