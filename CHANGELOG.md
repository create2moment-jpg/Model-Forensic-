# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org); the HTTP API is versioned under
`/api/v1`, stored documents carry a schema version and are migrated on read.

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
