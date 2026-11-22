# 05 — Validation

Goal: prove the adaptation works — with commands run, not with expectations.

```bash
mf setup check --live --json      # every requirement; the adapter contract per workspace; exit 0 = nothing blocking
mf projects check <id> --json     # contract v1 per workspace, check by check
mf doctor --live --json           # Azure: token → listing → one run's stages / metrics / files → name mapping
mf inspect <folder> --json        # exported files: what is recognised, drift notes
```

Then a smoke test of the forensic workflow on one real run (read-only):

1. `mf search "" --limit 5` (or the Runs page with the project selected) shows the project's runs.
2. `mf show <run>` — stages, metric keys (canonical), configs, files.
3. Open the run in the UI: Overview, Metrics, Config, Dataset / Compute, Artifacts; *Plan prefetch* lists its files.
4. `mf check` — Check my run produces a verdict.

Record the outcome: `mf setup check` writes `config/setup/compatibility.local.json` (git-ignored) with the Model
Forensic version, contract version and every result. Report exactly what was discovered, changed, validated and left
unresolved — including failed checks.
