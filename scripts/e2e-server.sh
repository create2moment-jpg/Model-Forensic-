#!/usr/bin/env bash
# Demo server for the Playwright journeys: fresh data dir, deterministic demo runs, built UI.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d -t mf-e2e-XXXX)"
cd "$work"
export MF_HOME="$work"   # isolated: config/, .mf/ and the knowledge inbox live in the temp folder, not the checkout
mkdir -p config
mf demo >/dev/null
mkdir -p config/knowledge/docs/training
cat > config/knowledge/docs/training/uncertainty-weighting.md <<'MD'
# Uncertainty weighting

## Why the weighted loss can be negative

The `+ s` tax is negative whenever sigma < 1, so a negative total is not divergence.
MD
MF_LOCAL_LLM_URL="http://127.0.0.1:9/v1" MF_WEB_DIST="$here/web/dist" exec mf serve --port "${MF_E2E_PORT:-8799}"
