#!/usr/bin/env bash
# Provider-aware, exact-session relay. Exit0 means backend acceptance was verified;
# exit1 no live holder; exit2 usage/config; exit3 cannot verify/deliver; exit4 outcome unknown.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/relay_to_orchestrator.py" "$@"
