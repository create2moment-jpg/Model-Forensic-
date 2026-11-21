#!/usr/bin/env bash
# Demo server for the Playwright journeys: fresh data dir, deterministic demo runs, built UI.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d -t mf-e2e-XXXX)"
cd "$work"
mf demo >/dev/null
MF_WEB_DIST="$here/web/dist" exec mf serve --port "${MF_E2E_PORT:-8799}"
