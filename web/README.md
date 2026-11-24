# Model Forensic — web UI

React 18 + TypeScript (strict) + Vite, TanStack Query for server state, uPlot for charts
(canvas, handles 100k+ points per series), lucide icons, hand-written CSS design tokens with
dark/light themes. No component framework: the UI is dense, fast and fully keyboard-driven.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api → http://127.0.0.1:8765 (run `mf serve`)
npm run build      # → web/dist, served by `mf serve` at http://127.0.0.1:8765/
npm run typecheck
```

## Screens

| Route | What it does |
|---|---|
| `/studies`, `/studies/:id` | study one experiment or a small group: evidence board (metric matrix vs baseline, architecture diagnostics, config differences), hypotheses with evidence / proposed change / test run / verify / status, AI investigations attached to the study, journal and timeline, Markdown export |
| `/` Today | your newest run's verdict, counts, recent runs, failures needing attention, what is running, runs you watch, starred experiments, open studies |
| `/runs` | search (DSL + natural language with visible interpretation), quick filters, sortable table, configurable metric columns, favorites, multi-select → compare / bulk download |
| `/runs/:id` | the **verdict** first; five places — Summary · Metrics (Explorer, Plots, Uncertainty) · Review (Expert review, Findings, Precision & recall) · Files (Config, Logs, Artifacts, Dataset, Compute, Setup, Sequences) · Timeline; `?tab=` names the view (old links work); **Compare**, **Ask** (drawer) and `⋯`. Views: **Overview** (headline metrics with z-scores vs baseline, *What changed?*, details, lineage) · **Metrics** (multi-metric charts, smoothing, log-Y, baseline overlay, synced cursors, zoom, PNG/CSV export, slice breakdowns) · **Findings** (run analyses, evidence, hypotheses, experiments, provenance, plots) · **Config** (flattened, filter, diff vs baseline) · **Logs** (regex search, error/warn highlighting, jump to error, download) · **Artifacts** (profiles with size estimates, cache downloads with live progress, per-file browser download) · **AI** |
| `/progression` | overlay loss/metric curves of up to 30 runs; x = step / epoch / hours since start; colour by run, user, experiment or a differing config value; live refresh of running jobs; per-run ranking table |
| `/compare` | noise-aware deltas with diverging bars, semantic config diff, overlaid curves and slice breakdowns |
| `/ai` | grounded investigations: searchable picker over 91 templates with variable inputs and live preview, extra runs in scope (from Progression), personas, claims with verification state, evidence chips, tool-call log, provenance, history |
| `/library` | analysis packs (params, limitations) and versioned prompts |
| `/reports` | generate, preview, download Markdown, print to PDF |
| `/settings` | BYOK: OpenAI / Azure OpenAI / vLLM / Ollama presets, session-only key, test connection |
| `/diagnostics` | latency p50/p95, index and cache stats, audit log |

The workspace dropdown in the top bar scopes every search, list and picker to one configured workspace, shows each
workspace's runs and last sync, and syncs one with a click.

Navigation comes from one source, `src/lib/places.ts` (places, their sections, which pages belong where); Settings →
Navigation → *Classic* shows every page as its own sidebar entry. Appearance (`src/lib/appearance.ts`): themes as
`data-theme` / `data-mode` on `<html>`, `data-motion` and `data-expressive` for motion and emoji.

Keyboard (`src/lib/shortcuts.ts`, `?` shows them all): `⌘K`/`Ctrl+K` or `/` palette · `g h` Today · `g r` runs ·
`g l` local folders · `g c` compare · `g p` progression · `g t` studies · `g k` check a run · `g a` ask · `g s`
settings · `.` the run page's `⋯` · runs table `j` `k` Enter `x` `s` `c`.

Screenshots in `docs/screenshots/` are regenerated with `MF_SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts`.
