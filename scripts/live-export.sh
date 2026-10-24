#!/usr/bin/env bash
# Refresh the live gate export by hand (LUCY-wqn96 / p122j). Usage: scripts/live-export.sh <repo> <sha>
#
# Writes <sha>'s GATE_PATHS to <git-common-dir>/live/<sha>/ (read-only, checked blob by blob)
# and swaps live/current to it. The reference-transaction hook does this on every main move;
# this is for a failed refresh. The logic and the gate list live in the INSTALLED hook (the
# committed copy install.sh made), so this wrapper never runs anything from the checkout.
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: $0 <repo> <sha>" >&2; exit 2; }
repo="$1"
sha="$2"
common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)"
hook="$common/hooks/reference-transaction"
[ -x "$hook" ] || { echo "live-export.sh: no installed hook at $hook; run scripts/git-hooks/install.sh" >&2; exit 1; }
cd "$repo"
exec "$hook" --export "$sha"
