# Next steps: prompts for upcoming sessions

**Target:** a product that experts trust, intermediates learn from, beginners can use safely, and that makes a
strong first impression. **Order (decided): features and infrastructure first, then improve.** Build every feature and the team
infrastructure on the demo data and the v1-derived formats; afterwards validate on real data, harden,
calibrate and polish. Run the prompts in this order:

| # | Prompt | Kind |
|---|---|---|
| 1 | P3.1 Beginner "Check my run" | feature |
| 2 | P3.2 Intermediate study wizard | feature |
| 3 | P3.3 Expert power features (notebook bridge, plugins, `mf gate`, replicate stats) | feature |
| 4 | P4.2 AI copilot (chapter narratives, ask-about-this, next-experiment diff, AI eval harness) | feature |
| 5 | P5.1 Shared deployment (server mode, Entra ID, scheduler, notifications, shared studies) | infrastructure |
| 6 | P4.1 First-impression polish (command center, tour, story strip, book cover/PDF, media) | feature + polish |
| 7 | P1.1 / P1.2 Real-data validation and study reproduction (tooling `mf inspect` already in place) | improve |
| 8 | P2.1 Hardening, P2.2 Calibration | improve |
| 9 | P5.2 Release quality (1.0) | improve |

While building features, keep the non-negotiables (tests green, no secrets, read-only, grounded AI) — stability
work later must not require rewriting features.

Each prompt below stands alone: paste it into a fresh session together with the repository. Do them in order;
each ends with tests and a push.

---

## ▶ RESUME HERE — paste this whole block into the next session

```
You are continuing development of Model Forensic in repo create2moment-jpg/Model-Forensic-,
branch claude/new-session-9inc8m (push there; do not open a PR unless asked).

Read first (in this order): docs/next-steps-prompts.md (this file: order + progress table),
README.md, docs/architecture.md, config/README.md. Then skim the code map below.

What exists (all tested, green):
- backend/src/model_forensic: FastAPI app (api/app.py), service (application/service.py: search, series with
  pipeline-stage merge, analyze, downloads, eval_tables, eval_json, lr_series), studies (application/study.py),
  "Check my run" (application/check_run.py), study book (reports/study_book.py + reports/svgplot.py),
  offline inspect (application/inspect.py), site interface (interface/azure.py, interface/team.py),
  Azure connector (infrastructure/connectors/azureml/*, run-history path from the v1 tool), name mapping
  (infrastructure/connectors/naming.py), analyses (analysis/plugins/*: training_dynamics, loss_forensics,
  regression, config_attribution, sparse_detection, architecture, heads, pipeline, study), study checks P1–P8
  (analysis/study_checks.py), eval JSON / parquet readers (analysis/evaljson.py, analysis/sequences.py),
  demo data (infrastructure/demo/synthetic.py: xf-pipe-finetune pipeline vs xf-online-baseline etc.).
- web/src: React + TS strict; pages (Check, Home, Runs, RunDetail + run/*, Compare, Progression, Studies,
  Study + study/*, Ai, Library, Reports, Settings, Diagnostics); components (Layout with Simple/Standard/Expert
  mode, AzureWorkspaces, Investigation, Chart, ...); api/client.ts + api/types.ts.
- instrument/: training-side kit (PyTorch, MMEngine, mmcv 1.x hooks).

How to run/test:
  cd backend && . ../.venv/bin/activate (or: uv venv && uv pip install -e ".[dev]")
  ruff format src tests && ruff check src tests && mypy src && python -m pytest -q
  cd web && npm ci && npx tsc -b && npx vite build
  demo: mkdir /tmp/mf && cd /tmp/mf && mf demo && MF_WEB_DIST=<repo>/web/dist mf serve --port 8771
  screenshots: Playwright (require('playwright'), chromium.launch()), deviceScaleFactor 2 for README images
  into docs/screenshots/.

Rules: read-only towards Azure; no secrets / subscription ids / internal hostnames / company code in the repo
(site details only in git-ignored config/*.local.yaml); deterministic analyses first, AI only grounded;
every change with tests; keep UI design tokens, dark/light, keyboard shortcuts; small commits; push after
each prompt; update the progress table in docs/next-steps-prompts.md after each prompt.

State: 1.0.0rc1. All planned features and the improve phase (P2.1 hardening, P1.2 study reproduction, P2.2
calibration, P5.2 release candidate) are DONE. The user decided against a real-data feedback loop: connection
issues on the real workspace are fixed locally with AGENTS.md + `mf doctor --live --json --bundle support.json`.
Before release 1.0: tag v1.0.0rc2 (current version) (the release workflow builds wheel, web bundle, SBOMs and the GHCR image) only
when the user asks. Further work = the "still open" column of the progress table, highest value first:
cross-run epoch budget in the book, benchmark scenarios for evaluation analyses,
comments with @mentions, Helm / Container Apps template.
Report at the end of each step what was done and what is open.
```

---

## Progress (update this table at the end of every session)

| Step | Status | What was done | What is still open |
|---|---|---|---|
| Foundations (before Phase 1) | ✅ done | Azure interface (`mf init`, Settings), pipelines & stages, downloads in the v1 layout, per-sequence parquets, team policy, `mf doctor --live`, study checks P1–P8, loss ledger, failure localisation, study book (`mf study book`) | — |
| **P1.1 Real-data validation** | 🟡 **in progress — tooling done, needs your data** | `mf inspect <folder>`: offline check of any downloaded artefact folder (the v1 tool's `outputs/_azure_artefacts`, a Model Forensic download, a run folder) with the same readers the analyses use — metric names → canonical keys (lists unmapped ones), configs (ruler, checkpoint monitor, Python YAML tags), per-class evaluation JSON families per class, **format-drift notes** ("recognised but no values extracted"), unrecognised evaluation JSONs, degenerate GT, parquet schemas; `--json` for sharing. Reads the v1 `metric_histories.json`. Format-drift tests (`tests/unit/test_inspect_drift.py`): corrupt/changed/unknown files → notes, never crashes. | **Your part:** run `mf inspect <your outputs/_azure_artefacts>` and `mf doctor --live`; send the outputs + sanitised samples. **Next session:** turn them into fixtures under `backend/tests/fixtures/real_layout/`, fix every note/unmapped name, then P1.2. |
| P1.2 Reproduce the hand-written study | ⏳ open (after the features, see order above) | Study book already reproduces the ruler-artifact verdict on the demo | Run on the real 1V5R runs; per-epoch attribution, finetune budget shift chapter, confusion matrix, distance-bucket compatibility |
| P3.1 Beginner "Check my run" | ✅ done (core) | `/check` page + `GET /api/v1/check` (`application/check_run.py`): safe analysis subset, ≤ 5 cards (traffic light, meaning, why it matters, what to do, learn-more with evidence + glossary terms), plain titles for checks P1–P8, overall verdict, default = newest completed run, auto baseline; `GET /api/v1/glossary` (23 terms) + glossary drawer; Simple / Standard / Expert switch (simple hides advanced pages); `g k` shortcut; tests `test_check_run.py`; README screenshot | "Explain this chart" button on every chart; "pick my latest job" filtered by the signed-in az user; short explainer pages behind "learn more" |
| P3.2 Intermediate study wizard | ✅ done | `application/study_templates.py` (5 templates with claim, falsifier, confounders, analyses); `Study.preregistration / gate / decision` (domain/study.py); `StudyService.create_from_template / check_confounder / run_gate / decide` (verdict blocked by failing measurement checks P1/P2/P3/P6 unless an override reason is given; verify() warns when blocked); API `GET /study-templates`, `POST /studies/wizard`, `POST /studies/{id}/gate`, `PUT /studies/{id}/confounders`, `PUT /studies/{id}/decision`; Markdown export + study book show decision, pre-registration, checks; UI: `/study-wizard` (Question → Runs → Pre-register → Create) and a **Decide** tab; tests `test_study_wizard.py`; README screenshots | replicate picking (seeds) in the wizard; per-template default deciding metric from the team policy |
| P3.3 Expert power features | ✅ done | `model_forensic.connect()` notebook bridge (`notebook.py`: runs, series, wide, lr, checks, findings, per_class, sequences, compare, noise, effects → pandas; extra `[notebook]`); local plugins (`analysis/local_plugins.py`, `plugin_dirs` setting, `config/plugins/example_analysis.py.example`, `mf plugins`, `POST /analyses/reload`); launch gate (`application/gate.py`, `mf gate --against/--metrics/--json`, `POST /gate`); replicate stats (`application/noise.py`, `mf noise`, `GET /noise`); docs/notebook.md; tests `test_expert_features.py` | UI for noise/effects (N-run compare with pinned metric sets per team); plugin hot-reload button in Diagnostics |
| P4.2 AI copilot | ✅ done | `ai/copilot.py`: grounded chapter narratives in the study book (`--ai`, only verified claims kept, removed count shown), `POST /findings/{id}/ask` (focused grounded investigation), `POST /findings/{id}/next-experiment` (rule-based from the config diff, else model-proposed with keys validated against the run's config; YAML patch); UI buttons on every finding; `ai/eval_harness.py` + `mf ai-eval --out docs/ai-eval.md --min-grounded` (12 questions; offline baseline: 100 % grounded, 0 hallucinated numbers); tests `test_copilot.py` | "Ask about this chart" on charts; run `mf ai-eval` with the real models (incl. local LLMs) and add the tables to docs/ai-eval.md; add it to CI with a threshold |
| P5.1 Shared deployment | ✅ done | `server:` settings (local default); auth `header` (oauth2-proxy / Easy Auth) or `oidc` (PyJWT, JWKS, issuer/audience/expiry) in `api/auth.py`, allowed domains/users, admin-only settings; per-request identity (`shared/identity.py`) → study owner, note authors, decision maker, timeline actors; `application/scheduler.py` (incremental sync, alerts for newly failed runs, nightly books) + `infrastructure/notify.py` (Teams webhook from an env var); `GET /me`, `GET /scheduler`; `mf backup`; `mf serve` refuses shared mode without auth on a public interface; Dockerfile (multi-stage, non-root, healthcheck, extra CA support in `deploy/ca/`), docker-compose with oauth2-proxy, `deploy/.env.example`, `deploy/model-forensic.shared.example.yaml`, docs/deploy.md; tests `test_shared_mode.py` | comments with @mentions and notifications to the mentioned user; regression alerts against a team baseline; Helm chart / Azure Container Apps template |
| P4.1 Polish & wow | ✅ done | Command center: *Insight on your newest run* (compact story strip from Check my run), *Running now*, *Studies* cards; guided **tour** (5 steps, `?tour=1` to replay, remembered in localStorage); run overview **story strip** (verdict light, top problems, next step, Details); study book **cover** chapter (question, verdict, key numbers) + **Print / save as PDF** with print CSS; Docker image verified end to end (build, `/api/v1/health`, UI) — data dir defaults to `/data` in the image; README media refreshed (command-center, run-story, study-book-cover) | 60-second demo GIF; empty-state illustrations; explain-this-chart |
| I1 Connection self-repair kit | ✅ done | `azureml/diagnose.py` (15 causes, each rule tested with a realistic message; preflight: packages, `az` login, proxy/CA env, placeholders); `mf doctor --live` prints cause + fixes, `--json`, `--bundle` (redacted ids/e-mails/SAS); `POST /backends/{name}/check` job + *Test connection* in Settings; diagnosis on `/backends`, 503 errors and in the UI error box; `GET /health/deep` + Status card in Diagnostics; generic 500 handler (no traces, request id), `ValueError` → 400; `AGENTS.md`, `docs/troubleshooting.md`; tests `test_diagnose.py`, `test_hardening.py` | — |
| I2 Hardening (P2.1) | ✅ done | M4 downsampling (spikes and NaNs survive; `points_total`, `non_finite` per series); 30 s TTL cache for running runs (series, files, run) instead of no cache; partial results when one pipeline stage fails; `scripts/loadtest.py` (2,000 runs + 5M-point run: all budgets met, see docs/development.md); Playwright suite `web/e2e/journeys.spec.ts` (13 journeys, fails on console errors) + `scripts/e2e-server.sh`; CI job `e2e` (small load test + journeys) | — |
| I3 Reproduce the hand-written study (P1.2) | ✅ done | docs/study-reproduction.md: 12 of 15 conclusions reproduced, 2 partly, 1 missing. New: P8 in epochs + epochs after the plateau (`max_epochs` / epoch series); stage change by metric family with the "useless on the headline but better localisation/tracking" verdict and the P3 velocity-artifact caveat; *budget reallocation* chapter (ledger per training stage, share shift in points); *Capacity* chapter (signals for/against → verdict + decisive experiments); tests `test_study_reproduction.py` | queries-vs-detections surplus; multi-run "more input did not help" capacity signal |
| I4 Calibration (P2.2) | ✅ done | `calibration.py` + `mf calibrate` → docs/calibration.md (270 labelled synthetic scenarios incl. heavy-tailed noise, warm-up, step/cosine LR; precision/recall per threshold). The benchmark found and fixed 3 detector flaws: P8 called a planned cosine decay "schedule-bound" (56 % precision → 100 %; new `schedule_min_remaining`), divergence used the raw minimum (83 % → 100 %; smoothed minimum), spikes fired on heavy-tailed noise (57 % → 100 %; `spike_min_rel` 0.4). Check thresholds are now `study_checks` params (team policy). `GET /findings/{id}/explain` (*Why this confidence?*), `PUT /findings/{id}/feedback`, `GET /feedback/report` (false-alarm rate per analysis, in Diagnostics). Tests: `test_calibration_defaults_hold`, feedback tests | benchmark scenarios for evaluation analyses (sequence drill-down, eval localisation, regression with replicates) |
| I5 Release candidate (P5.2) | ✅ done | 1.0.0rc1; CHANGELOG.md; hash-pinned `backend/requirements.lock` used by the Dockerfile (image verified: build, health, UI); `.github/workflows/release.yml` (tag → tests, calibration gate, wheel/sdist, web bundle, CycloneDX SBOMs, GHCR image with SBOM + provenance, GitHub release); security review: YAML alias-bomb + size limits (`shared/yamlsafe.py`), malformed configs skipped in both connectors, CSP embedded in the book file, security headers, traversal tests (`test_security.py`); docs site (docs/index.md, guide/getting-started, studies, reference, faq; mkdocs.yml); onboarding test from a fresh clone → fixed: demo count in README, added `mf check` | tag v1.0.0rc1 when you want the release built |
| F1 Explain this chart | ✅ done | `analysis/chart_insights.py` (trend, best + where, plateau, spikes/dips (calibrated thresholds), first NaN, LR drops, noise level, baseline delta with a noise check, rule-based *what to check*); `GET /runs/{run}/metrics/explain`, `POST /runs/{run}/metrics/ask` (grounded on the reading; offline → the checks); UI: *Explain* on every metric chart with markers (labels never overlap), NaN/Inf badge, reading panel, ask box; tests `test_chart_insights.py` + e2e journey 14 | per-chart AI answers in the study book |
| F2 Explain multi-run charts | ✅ done (1.0.0rc2) | `explain_runs` (ranking, running runs excluded, top-two tie vs noise, spread vs noise, NaN / worsening runs, first/last to flatten, still improving); `POST /metrics/explain-runs`, `POST /metrics/ask-runs`; *Explain* on every Progression chart with a ranking table; tests + e2e journey 15; version 1.0.0rc2, CHANGELOG | — |
| F3 Compare 2–10 runs: where and why they diverge | ✅ done | `analysis/divergence.py` (alignment, smoothing, noise threshold, sustained divergence point, outcome ahead/behind/closed/crossed/same, sparse point-by-point mode, LR events / LR difference at the divergence point, NaN onset, config changes ranked by how they fit the timing); `POST /compare/curves`, `POST /compare/ask`; UI: Compare section with run chips + *Add a run*, chart with divergence markers (auto log scale), verdict table; Runs table / Progression pass up to 10 runs; fix for right-aligned marker labels; tests `test_divergence.py` + e2e journey 16 | divergence across pipeline stages; — |
| R1 First real-workspace test (user) | ✅ fixed | pipeline stages not resolved for indexed runs (root cause of 0 metrics / only fast-debug + logs downloads); local LLM compatibility (scheme-less URL, proxy bypass for loopback, JSON-mode / tools fallback, `<think>`, context message, capability probe); `user:me` + *Mine*; study drafts persist, cancel/discard, close/reopen; tests `test_pipeline_resolution.py`, `test_llm_local_server.py`, `test_my_runs.py`, e2e 17 | user to re-test on the real workspace |
| V1 parity: Dataset + Compute | ✅ done | `analysis/provenance.py` (v1 parsers + per-stage + requested≠loaded check), `application/run_logs.py` (streaming scan of every log of a run and its stages), `application/run_info.py`, `GET /runs/{run}/dataset`, `GET /runs/{run}/compute`, tabs *Dataset* / *Compute*; tests `test_provenance.py` (v1 cases) + e2e 18 | — |
| V1 parity: Precision & recall | ✅ done | `application/precision_recall.py` (working points with target vs measured recall + shortfall, AUC by distance, parquet counts, log search, eval-file guide), preview endpoint (bounded, no inline SVG/HTML), tab *Precision & recall*; tests `test_precision_recall.py` + e2e 19 | next v1 gaps: artifact catalog / triage before download, compare stage mapping + R1/R2 aliases, AI summary at four depths, book theme toggle |
| N1 Is the difference real? + save to study | ✅ done | seed-replicate noise floor in `compare_curves` (σ against the reference's seeds, verdict per run), `POST/DELETE /studies/{id}/comparisons`, `Study.comparisons`, study-book chapter, Compare *Save to study*, study side panel; tests in `test_divergence.py` + e2e 20 | — |
| N2 Artifact catalog / triage before download | ✅ done | `application/catalog.py` (families with answers / worth / caveat, counts, sizes, cached, classes, stages), `GET /runs/{run}/catalog`, Artifacts tab *What's in this run* with per-family download; tests `test_catalog.py` + e2e 21 | — |
| N3 Compare stage mapping + aliases | ✅ done | `stages` map in `/compare/curves` (+ saved comparisons), per-run stage picker on Compare, single-evaluation comparison in `diverge`, R1/R2 aliases; tests + e2e 22 | raw text config diff (v1) |
| N4 Jobs survive a restart | ✅ done | `JobRegistry` (doc store, 2 s flusher, interrupted on restart, last 200 kept), job `params`, `POST /jobs/{id}/rerun`, *Run again* in the jobs tray; test `test_job_persistence.py` | — |
| N5 Epoch budget across runs | ✅ done | `study_checks.epoch_budget`, book *Convergence → Epoch budget across runs* (+ verdict), demo online baseline with a longer schedule; tests in `test_study_reproduction.py`; study-reproduction 13/15 | queries-vs-detections surplus; multi-run "more input did not help" |
| K1 Knowledge block | ✅ done | `knowledge/` (entries: constraint / fact / setup / caveat / code_ref; `when` setup, dataset, stage, config globs, tags; effects ignore metrics / classes, caveat, setup label; proposed → active → retired), `KnowledgeStore` (git-ignored `config/knowledge/*.local.yaml`), `GET/PUT/DELETE /knowledge`, `POST /knowledge/import`, `GET /runs/{run}/knowledge`, page *Knowledge*; examples (manual labels → no velocity/accel, Animal set aside, setup labels); tests `test_knowledge*.py` | — |
| K2 Apply knowledge | ✅ done | per-stage contexts (a GT finetune stage and its pretrain differ); findings, overview, Check my run and precision & recall mark what an entry sets aside (kept, with the entry id and why) | study book chapters (B1) |
| H1 Harness adaptation | ✅ done | `harness/` (README, task prompts: setup profiles, loss composition, uncertainty weighting, label targets, metric definitions, checkpoint & validation; rules), `harness/knowledge.schema.json`; `mf harness tasks|brief|schema`, `mf knowledge list|validate|import` (imports as *proposed*), `GET /harness/tasks`, `GET /runs/{run}/harness-brief`; Knowledge page *Learn from the codebase*; Docker image ships `harness/`; AGENTS.md section; tests `test_harness.py` | — |
| B1 Study book: baseline → comparisons → conclusions | ✅ done | `analysis/baseline_study.py` (loss progress per family on the raw loss, uncertainty weights per term, config vs behaviour, effective config of the training stage, submitted vs composed disagreements), `reports/book_parts.py` (Part 1 baseline chapters, Part 2 one chapter per run with config diff / overlays / σ moves / divergence / shared-ruler deltas, Part 3 conclusions), knowledge applied in every part (+ appendix shared ruler / failures), wrapping chart legend, `_datasets` ignores a disabled local dataset; tests `test_baseline_study.py` | user to read a real baseline study and say which readings are missing |
| M1 Knowledge sources, inbox, documentation index | ✅ done | `knowledge/sources.py` (sources from config + `MF_KNOWLEDGE_SOURCE_*`, inbox `MF_KNOWLEDGE_INBOX`, pruned walk, safe join), `KnowledgeStore` over all sources (fingerprint cache, trust → proposed, overrides in the inbox, origins), `knowledge/docindex.py` (FTS5 sections, incremental, any-word fallback), API `/knowledge/sources`, `/docs/search|files|file`, CLI `mf knowledge sources|where`, `mf docs index|search`, brief *Where to write* + related docs, UI Knowledge tabs Entries / Documentation / Sources, safe Markdown viewer; ADR-0009; tests `test_knowledge_sources.py` + e2e 23 | M2 persistence at scale, M3 server-side drafts |
| M2 Persistence at scale | ✅ done | `DocumentStore` summary column (+ lazy backfill), `list_summaries` (json filters, text, paging, total), WAL / busy timeout / locked reads, study summaries + `GET /studies?status&q&limit&offset` (`X-Total-Count`), bounded investigations list; ADR-0010; tests `test_persistence_scale.py` (600 studies < 0.3 s, concurrent writers/readers) | — |
| M3 Drafts + continue where you left off | ✅ done | `application/drafts.py` (per-user drafts, validation, 256 KiB cap, prune 90 days / 200 per user), `/drafts`, `/work`; web `useServerDraft` (autosave, flush on leave, no save before load), New study + guided study on server drafts with one-time localStorage migration, Drafts list, Studies filters/search/load more, Command center *Continue where you left off*; tests `test_drafts.py` + e2e 17, 24 | compare page and question boxes as drafts |
| E1 Experience (beliefs checked against runs) | ✅ done | `knowledge/model.py` `BeliefTest` + kind `belief`; `knowledge/beliefs.py` (pairing with fewest other differences, confound weights, seed-noise threshold, Beta posterior with weak prior + evidence-only estimate, colours, counterexample, next experiment, `pair_outcome`); `application/beliefs.py` (facts per run cached, pipeline stages not double-counted, verdict history, staleness, vocabulary); API `/beliefs`, `/beliefs/vocabulary`, `/beliefs/{id}`, verify jobs; study book *Your experience, checked on this comparison*; UI Knowledge → Experience (bubbles, filters, interval bar, editor with autocomplete, detail); ADR-0011; tests `test_beliefs.py` + e2e 25 | LLM-proposed tests for free-text beliefs; more test types (metric relation, range/class claims) |
| R2 Prefetch wheel + my experiments | ✅ done | `infrastructure/persistence/meta_cache.py` (gzip JSON per run/kind, atomic, LRU byte budget, corrupt = miss), service disk tier under the LRU caches for finished runs, `prefetch()` / `is_prefetched()`, prefetch jobs; `experiment:mine` (`SearchFilters.experiment_owners`), `GET /experiments`; Runs table prefetch button (cloud / wheel / tick / failed), bulk *Prefetch*, *My experiments* quick filter, Experiment picker; tests `test_prefetch.py` (restart with the backend down) + e2e 26 | expandable pipeline rows in the Runs table |
| R3 LLM plug + status | ✅ done | `application/llm_connect.py` (discover `/models`, test chat, switch only on success, ping status, `llm.json` persistence restored in `build`), `local_llm_url` setting, `/settings/llm/ping`, `/settings/llm/connect-local`, health `llm_status`; header `LlmBadge` (dot + plug); tests `test_llm_connect.py` (real HTTP server on 127.0.0.1) + e2e 27 | — |
| P1.1 Real-data validation | ⏸ by decision | user fixes connection/format issues locally with AGENTS.md + `mf doctor --live --bundle` + `mf inspect` | — |

**How to start the next session** (paste after the context block):
```
All features (P3.x, P4.x, P5.1) are done. Start the improve phase from docs/next-steps-prompts.md:
P1.1 with the attached `mf inspect --json` / `mf doctor --live --json` outputs (if provided), else P2.1
hardening. Tests for every change; update the progress table; push after each prompt.
```

**How to resume P1.1 later** (when its turn comes; paste after the context block):
```
Continue P1.1 from docs/next-steps-prompts.md. Here is the output of
`mf inspect <my artefact folder> --json` and `mf doctor --live --json`: <paste>.
Sanitised samples: <upload>. Turn the samples into fixtures, fix every unmapped name and every
format-drift note, add regression tests, update the progress table, push.
```

---

## Context block: put this in front of every prompt

```
Repository: create2moment-jpg/Model-Forensic- (branch claude/new-session-9inc8m or main).
Model Forensic = local, read-only forensic workbench for Azure ML training runs
(backend: Python/FastAPI in backend/, web: React+TS in web/, training kit: instrument/).
Read first: README.md, docs/architecture.md, config/README.md, docs/next-steps-prompts.md.
Non-negotiables:
- Read-only towards Azure. No secrets, subscription IDs, internal hostnames or company code in the repo;
  site details live only in git-ignored config/*.local.yaml.
- Deterministic analyses first; AI only on cited evidence (grounding checker stays on).
- Every change: ruff + mypy (backend), tsc (web), pytest all green; add tests for every bug fixed.
- Keep the UI consistent with the existing design tokens (dark/light), keyboard shortcuts, no layout regressions.
- Commit in small steps with clear messages; push to the working branch.
```

---

## Phase 1: Real-data validation (highest value, do first)

### P1.1 Validate against the real workspace
```
Goal: make every feature work on my real Azure ML runs. I have run `mf init` and `mf doctor --live`;
here is the output: <paste>. Here are sanitised samples: one composed_config.yaml, the metric-name list of one
training stage, and one class folder of the evaluation JSONs (torch_metric_evaluation, pr_curves_detailed,
false_positives_analysis, bb3d_match_evaluation, working_point) and one per-sequence parquet schema: <paste/upload>.
1. Turn each sample into a sanitised test fixture under backend/tests/fixtures/real_layout/ (rename classes/IDs).
2. Fix every parsing gap: metric naming (connectors/naming.py), evaljson.py key names (especially the
   bb3d_match_evaluation bin structure and false_positives_analysis), stage names, LR column, ruler extraction.
3. Make `mf doctor --live` report 100% of my metric names mapped or explicitly listed as intentionally unmapped.
4. Add a "format drift" test: unknown keys/files must produce a visible note, never a crash or a silent zero.
Deliver: fixtures, fixes, tests, and a short list of everything that did not match my assumptions.
```

### P1.2 Reproduce the hand-written study
```
Goal: the study book must reproduce my 1V5R training study (chapters/ from the earlier zip; I attach the
markdown again). Create a study with my four runs (+ the online baseline), generate `mf study book`, then
compare chapter by chapter with my hand-written conclusions (ruler artifact, early plateau at high LR, finetune
budget shift, missing validation, muted radar aux head, far-range VRU, FP tails, degenerate GT velocity).
For each conclusion: reproduced / partly / missing / contradicted, with the reason. Fix what is missing
(e.g. per-epoch attribution from declared epochs, finetune budget reallocation chapter, attribute confusion
matrix, distance-bucket compatibility before comparing rulers). Add a regression test that runs the book on the
sanitised fixtures and asserts the verdict and the check statuses.
```

---

## Phase 2: Stability and trust

### P2.1 Hardening pass
```
Goal: production-grade stability. Do a systematic hardening pass:
- Error handling: every API endpoint returns a typed error with a user-readable message; no 500 with a stack
  trace; the UI shows actionable messages (what failed, what to do).
- Timeouts, retries and cancellation for every Azure call; partial results instead of failure when one stage or
  workspace fails; jobs survive page reloads.
- Performance: profile with 2,000 indexed runs and runs with 300+ metric series of 100k points (generate
  synthetic load); keep search < 50 ms, run page < 1.5 s from cache, charts downsampled server-side.
- Caching: TTLs for running vs finished runs; cache invalidation; disk budget respected.
- Add a /api/v1/health/deep endpoint and a "Diagnostics" view showing backend status, cache, last sync, errors.
- Add Playwright end-to-end tests for the 10 main user journeys (search, open run, compare, analyze, study,
  study book, download, settings/Azure, AI ask, progression) against the demo data; run them in CI.
Deliver: fixes, load-test script, e2e suite, CI green.
```

### P2.2 Calibrate the analyses
```
Goal: findings people can trust. For every analysis and check (study_checks, loss_ledger, task_weighting,
eval_localization, sequence_drilldown, stage_delta, regression, training_dynamics, decoder_layers):
1. List each threshold and its current value.
2. Build a labelled benchmark from my real runs (I will tell you which runs are known-good / known-bad and why)
   plus the synthetic demo; measure hits, false alarms and misses per check.
3. Tune thresholds, move them to analysis parameters with documented defaults, and expose them in the team policy.
4. Show a confidence explanation on every finding ("why this confidence", "what would change it").
5. Add a "this finding is wrong" feedback button that stores feedback locally and a report of false-alarm rates.
Deliver: benchmark script + results table in docs/calibration.md, tuned defaults, tests.
```

---

## Phase 3: Useful at every level

### P3.1 Beginner mode: "Check my run"
```
Goal: a beginner gets value in 2 minutes without knowing the concepts. Build a guided "Check my run" flow:
- Entry: paste a run id / pick my latest job (filter by signed-in user from az).
- Runs the safe subset of analyses, then shows at most 5 results as plain-language cards:
  traffic light, one-sentence "what this means", "why it matters", "what to do next", "learn more" (expandable,
  linking to a short explainer). Jargon (σ, ruler, ledger) only in the expandable part.
- A glossary drawer with 20 core terms, written for beginners, each with a tiny example.
- An "Explain this chart" button on every chart (deterministic text from the data; AI optional).
- Progressive disclosure: a simple / standard / expert toggle in the header that hides advanced tabs.
Deliver: the flow, explainer texts, tests; screenshots for the README.
```

### P3.2 Intermediate: guided study workflow
```
Goal: an intermediate engineer runs a proper study without supervision. Add a study wizard:
1. Pick a question template ("Is my change better?", "Why did it regress?", "Should I train longer?",
   "Is this head useful?", "Pretrain vs finetune").
2. The wizard pre-registers the hypothesis, the deciding metric (and its ruler), the falsifying evidence and
   the confounders to check first (lesson from the 1V5R study).
3. It picks baseline and replicate runs, runs checks P1–P8 first and blocks verdicts that a failing measurement
   check invalidates.
4. Ends with a decision record (what we decided, based on what) exported to Markdown and the study book.
Deliver: wizard UI, templates, tests.
```

### P3.3 Expert: power features
```
Goal: an expert is faster than with notebooks. Add:
- Query and notebook bridge: `from model_forensic import connect; mf = connect()` with pandas frames for
  series, evaluation tables and findings; documented in docs/notebook.md.
- Custom checks and analyses as plugins in a local folder (entry points), hot-reloaded, with a template.
- Pre-launch gate: `mf gate <config.yaml>` runs launch-time checks (ruler, monitor exists, validation loop
  configured, GT coverage of supervised attributes) before a job is submitted; CI-friendly exit codes.
- Sweep and replicate views: noise floor per metric from seeds, effect sizes with confidence intervals.
- Keyboard-driven compare of N runs with pinned metric sets saved per team.
Deliver: features, docs, tests.
```

---

## Phase 4: Wow effect and polish

### P4.1 First-impression demo and UX polish
```
Goal: someone who sees the tool for 3 minutes wants it. Do a polish pass:
- Landing "command center": my workspace at a glance (running jobs, recently failed with one-line cause,
  new regressions, studies in progress), each card one click from the answer.
- A 90-second guided tour on the demo data (first visit), skippable.
- Run page: a top "story strip" (training health → evaluation → top 3 findings → suggested next experiment).
- Motion and feedback: skeleton loaders, optimistic updates, toasts with undo, empty states with a next action.
- Visual consistency audit: spacing, typography, chart colours, dark and light modes, 1280–2560 px and mobile
  read-only; fix every overflow (screenshot every page with Playwright and review).
- Study book: cover page, print/PDF styling, chart hover values, a "share" export with all assets embedded.
- Re-record README screenshots and a short GIF of the main flow.
Deliver: polished UI, tour, refreshed README media.
```

### P4.2 AI copilot that experts respect
```
Goal: AI that saves time without making things up. Improve the investigator:
- Chapter narratives in the study book (--ai): per chapter, a grounded paragraph with citations; unverifiable
  sentences are removed or marked, never shown as fact.
- "Ask about this chart / finding" in place, with the evidence pre-selected.
- Suggest the next experiment as a ready-to-launch config diff (read-only: show it, don't submit it).
- Evaluation harness: 30 questions on the demo and real fixtures with expected cited answers; report grounding
  rate and hallucinated numbers per model (local LLMs included). Fail CI on regression.
Deliver: features, eval harness, results in docs/ai-eval.md.
```

---

## Phase 5: Team rollout

### P5.1 Shared deployment
```
Goal: the team uses one instance. Add:
- Server mode: Docker image, Entra ID login (OIDC), per-user identity on studies and notes, read-only service
  principal or user-delegated Azure access (documented trade-offs), HTTPS behind a reverse proxy.
- Background scheduler: incremental sync every N minutes, a nightly study book for configured studies,
  notifications (Teams webhook / email) for new failures and regressions.
- Shared studies with comments and mentions; audit log.
- Backups of the local database; upgrade migrations.
Deliver: deploy docs (docs/deploy.md), docker-compose, tests; keep the single-user local mode working.
```

### P5.2 Release quality
```
Goal: ship 1.0. Prepare:
- Versioning and changelog (CHANGELOG.md), release workflow, pinned dependencies, SBOM.
- Security review (input validation on all files read from runs, CSP, path traversal, YAML/JSON limits).
- Documentation site: getting started in 10 minutes (beginner), study workflow (intermediate),
  checks/ledger reference and plugin API (expert), FAQ and troubleshooting.
- Onboarding test: a new colleague follows the docs from scratch; record every stumble and fix it.
Deliver: 1.0 release candidate.
```

---

## Definition of "great product" (acceptance across phases)
- **Stable:** zero crashes on real runs over a month of use; every failure explains itself.
- **Correct:** reproduces hand-written studies; false-alarm rate per check documented and low.
- **Useful:** beginner gets a correct, actionable answer in under 2 minutes; intermediate completes a study
  without help; expert saves days per study.
- **Wow:** first 3 minutes show a real insight (ruler artifact, wasted epochs, muted head) on the user's own runs.
