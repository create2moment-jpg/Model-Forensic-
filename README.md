# Model Forensic

A forensic workbench for ML experiments (Azure ML first; backend-agnostic by design). It shortens
the path from **"something is wrong with this model"** to **"here is the evidence, the most likely
causes, the competing hypotheses, and the cheapest experiment that separates them."**

- **Find** runs instantly: local index, fuzzy search, a query DSL and natural-language queries
  (`failed training jobs from last week`, `runs using 8-frame temporal windows`).
- **Inspect** a run with an auto-selected baseline and a *What changed?* section.
- **Compare** runs with **replicate-aware** significance (seed variance as the noise floor) and
  **slice localization** (`recall regression concentrated in range=60-inf`).
- **Analyze** with deterministic forensic packs: training dynamics, loss forensics, regression,
  config attribution, sparse-detection health. Output is structured **findings** with evidence,
  hypotheses, recommended experiments and provenance.
- **Ask AI** (bring your own OpenAI-compatible key) with deterministic **grounding**: every claim must
  cite evidence, and numbers that aren't in the evidence are flagged as unverified.
- **Report**: Markdown / print-ready HTML forensic reports.

> Status: backend, HTTP API and CLI for phases 0–3 plus AI, prompts and reports are implemented and
> tested. The web UI is the next slice. The Azure ML adapter passes the connector contract suite
> against SDK fakes but has **not yet been validated against a live workspace**.
> See [`docs/architecture.md`](docs/architecture.md) §26.

## Quick start (demo mode, no cloud needed)

```bash
uv venv && source .venv/bin/activate        # or python -m venv .venv
uv pip install -e "backend[dev]"
mf demo                                      # writes 11 synthetic runs to .mf/demo-runs and indexes them

mf search "failed training jobs from last week"
mf show cand-radar-8f                        # overview + what changed vs auto-selected baseline
mf compare baseline-cl4f-s0 cand-radar-8f    # replicate-aware deltas, semantic config diff
mf analyze cand-radar-8f --baseline baseline-cl4f-s0
mf ask cand-radar-8f --baseline baseline-cl4f-s0 --prompt why_worse
mf report cand-radar-8f --baseline baseline-cl4f-s0 -o report.md
mf serve                                     # HTTP API on http://127.0.0.1:8765/docs
```

## Connect Azure ML and an LLM

```bash
uv pip install -e "backend[azure]"
az login                                     # or managed identity / workload identity
cp model-forensic.example.yaml model-forensic.yaml   # fill subscription / resource group / workspace
export MF_LLM_API_KEY=...                    # only the env var NAME is stored in config
mf doctor && mf sync
```

## Documentation

- [Requirements critique](docs/requirements-critique.md) — risks found in the spec and how the design responds
- [Architecture & product design](docs/architecture.md) — personas, journeys, components, interfaces, API, security, roadmap
- [ADRs](docs/adr/) — key decisions
- [Development guide](docs/development.md) — workflow, conventions, troubleshooting

## Layout

```
backend/src/model_forensic/
  domain/        vendor-neutral types (Run, MetricKey/Series, Finding, Evidence, ...)
  analysis/      deterministic computation: signal, comparison, config diff, plugins
  application/   ports + ForensicService
  ai/            context builder, grounding, prompt library, tools, investigator
  infrastructure/connectors (azureml, local), search, artifacts, persistence, llm, demo
  api/ cli/ reports/ observability/ shared/
backend/tests/   unit · integration · contract · e2e (83 tests)
docs/            architecture, critique, ADRs, development guide
```
