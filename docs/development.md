# Development guide

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e "backend[dev]"
cd backend && ruff format src tests && ruff check src tests && mypy src && pytest
```

## Conventions

* Python ≥ 3.10, `mypy --strict`, ruff (line length 120). Domain types are frozen pydantic models.
* Layers: `domain` ← `analysis` ← `application` ← `infrastructure`/`api`/`cli`. Never import
  infrastructure or vendor SDKs from domain/analysis. Wiring happens only in `container.py`.
* Errors crossing a layer boundary are `ForensicError` subclasses with a stable `code`.
* Log with `get_logger(__name__).info("msg", key=value)`; never log secrets or raw artifact content.

## Adding an analysis plugin

1. Create `analysis/plugins/<name>.py` with a class exposing `name, version, title, description,
   category, requires, limitations, Params` and `run(ctx, params) -> AnalysisResult`.
2. Use `FindingBuilder` for ids/provenance; label evidence correctly (OBSERVED vs COMPUTED vs
   INFERRED vs HYPOTHESIZED). Never do I/O inside `run`.
3. Add a **positive** fixture (the pathology is detected) and a **negative** fixture (a clean
   series is not flagged) in `tests/unit/test_analyses.py`.
4. Register in `analysis/registry.py` (built-in) or expose via the `model_forensic.analyses`
   entry point (third-party).

## Adding a backend connector

Implement `RunSource` (see `application/ports.py`), keep all SDK→domain mapping in a pure mapper
module, register it in `infrastructure/connectors/__init__.py`, and add it to the parametrized
fixture in `tests/contract/test_run_source_contract.py`.

## Metric conventions

`<split>/<name>[<dim>=<value>,...]`, e.g. `train/loss/cls`, `val/NDS`, `val/recall[range=60-inf]`,
`val/AP[class=pedestrian]`. Splits: train, val (or eval), test. Add metric directions/units in a
registry YAML:

```yaml
metrics:
  - {name: my_custom_score, higher_is_better: true, family: detection, min_meaningful_rel_change: 0.005}
```

Tag seed replicates with the same `group` tag to enable replicate-aware significance.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Azure ML support not installed` | `uv pip install -e "backend[azure]"` |
| `DefaultAzureCredential failed` | `az login`, or set `interactive: true`, or configure managed identity |
| Analyses say `skipped: no 'loss' training series` | metric names differ: log `train/loss` or pass `{"training_dynamics": {"loss_metric": "..."}}` |
| Regression findings say "Fewer than 2 baseline replicates" | tag baseline seeds with a shared `group` tag |
| `insufficient_disk` on download | lower the profile (`fast_debug`), raise `artifact_cache_max_gb` retention or free disk |
| AI answers marked UNVERIFIED | the model cited numbers not in evidence; try a stronger model or lower temperature |
| LLM `permission_denied` | the env var named in `llm.api_key_env` is missing or wrong |

## Contributing

Small PRs; each with tests and, for user-visible behaviour, a doc update. Architectural changes
need an ADR in `docs/adr/`.

## Quality gates

| Gate | Command | Budget / expectation |
|---|---|---|
| Unit + API tests | `cd backend && pytest` | green |
| Types and lint | `ruff check src tests && mypy src`; `cd web && npx tsc -b` | clean |
| User journeys (Playwright, demo data) | `cd web && npm run build && npm run e2e` (needs `mf` on PATH) | 13 journeys, no console errors |
| Load | `python scripts/loadtest.py --runs 2000 --series 50 --points 100000` | search < 50 ms, cached run page < 1.5 s, 2,000-point chart from cache < 300 ms |
| Container | `docker build -t model-forensic .` then `curl :8765/api/v1/health` | healthy, UI served |

Measured (2,000 runs + one run with 50 × 100k points, laptop-class CPU): search 11 ms, run list 16 ms, cached run
page 6 ms, cached 2,000-point chart 23 ms; the first open of the 5M-point run takes ~12 s (reading the local file).
Charts use M4 downsampling (`shared/downsample.py`): first/last/min/max per bucket plus the first non-finite point,
so spikes and NaNs survive. Running runs are cached for 30 s (`ForensicService.running_ttl_s`), finished runs until
evicted. Errors: typed `{"error": {"code", "message", "details"}}`; unexpected errors return a request id, never a
stack trace; Azure failures carry a `diagnosis` (cause + fixes).
