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
| `/` Command center | run counts, recent runs, failures needing attention, recent AI investigations |
| `/runs` | search (DSL + natural language with visible interpretation), quick filters, sortable table, configurable metric columns, favorites, multi-select → compare / bulk download |
| `/runs/:id` | **Overview** (headline metrics with z-scores vs baseline, *What changed?*, details, lineage) · **Metrics** (multi-metric charts, smoothing, log-Y, baseline overlay, synced cursors, zoom, PNG/CSV export, slice breakdowns) · **Findings** (run analyses, evidence, hypotheses, experiments, provenance, plots) · **Config** (flattened, filter, diff vs baseline) · **Logs** (regex search, error/warn highlighting, jump to error, download) · **Artifacts** (profiles with size estimates, cache downloads with live progress, per-file browser download) · **AI** |
| `/progression` | overlay loss/metric curves of up to 30 runs; x = step / epoch / hours since start; colour by run, user, experiment or a differing config value; live refresh of running jobs; per-run ranking table |
| `/compare` | noise-aware deltas with diverging bars, semantic config diff, overlaid curves and slice breakdowns |
| `/ai` | grounded investigations: searchable picker over 91 templates with variable inputs and live preview, extra runs in scope (from Progression), personas, claims with verification state, evidence chips, tool-call log, provenance, history |
| `/library` | analysis packs (params, limitations) and versioned prompts |
| `/reports` | generate, preview, download Markdown, print to PDF |
| `/settings` | BYOK: OpenAI / Azure OpenAI / vLLM / Ollama presets, session-only key, test connection |
| `/diagnostics` | latency p50/p95, index and cache stats, audit log |

The workspace dropdown in the top bar scopes every search, list and picker to one configured workspace.

Keyboard: `⌘K`/`Ctrl+K` or `/` command palette · `g r` runs · `g p` progression · `g c` compare · `g a` AI · `g s` settings · `g h` home.
