#!/usr/bin/env bash
# claude-seat-setup.sh — create/refresh extra Claude Team seat config dirs.
#
# Seat 1 is the default ~/.claude. Seats 2..N live in ~/.claude-seatN and share
# everything in ~/.claude (hooks, skills, settings, commands, agents, projects/
# transcripts + memory, sessions registry) via symlinks, so every seat boots as
# full Lucy and stays visible to fleet tooling. Only the login and per-account
# runtime state stay separate.
#
# Usage: claude-seat-setup.sh 2 3 4     (idempotent; re-run to pick up new entries)
# Then:  c2  → sign in with that seat's email ("Continue with email").
set -euo pipefail

SRC="$HOME/.claude"

# Account-tied or per-process state: never shared between seats.
is_private() {
  case "$1" in
    .credentials.json|.git|.gitignore|.DS_Store) return 0 ;;
    daemon|daemon.*|daemon-*|jobs) return 0 ;;          # bg daemon is per-account
    statsig|telemetry|stats-cache.json|usage-data) return 0 ;;
    policy-limits.json*|remote-settings.json) return 0 ;;
    session-env|shell-snapshots|paste-cache|cache|debug) return 0 ;;
    *) return 1 ;;
  esac
}

[ $# -gt 0 ] || { echo "usage: $0 <seat-number>..." >&2; exit 2; }

for n in "$@"; do
  [[ "$n" =~ ^[2-9]$ ]] || { echo "seat must be 2-9 (seat 1 is ~/.claude): $n" >&2; exit 2; }
  DST="$HOME/.claude-seat$n"
  mkdir -p "$DST"
  linked=0
  for path in "$SRC"/* "$SRC"/.[!.]*; do
    [ -e "$path" ] || continue
    name="$(basename "$path")"
    is_private "$name" && continue
    if [ -L "$DST/$name" ]; then continue; fi
    if [ -e "$DST/$name" ]; then echo "  seat$n: skipping $name (real file exists)"; continue; fi
    ln -s "$path" "$DST/$name"; linked=$((linked + 1))
  done

  # Global state file: copy shared prefs + MCP servers + project trust, never the account.
  if [ ! -f "$DST/.claude.json" ]; then
    python3 - "$HOME/.claude.json" "$DST/.claude.json" <<'EOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))
keep = ["mcpServers", "projects", "theme", "hasCompletedOnboarding", "lastOnboardingVersion",
        "autoUpdates", "autoUpdatesProtectedForNative", "installMethod", "githubRepoPaths",
        "shiftEnterKeyBindingInstalled", "claudeInChromeDefaultEnabled"]
json.dump({k: d[k] for k in keep if k in d}, open(dst, "w"), indent=2)
EOF
    chmod 600 "$DST/.claude.json"
    echo "  seat$n: wrote .claude.json (MCP servers + project trust, no login)"
  fi
  echo "seat$n: $DST ready ($linked new links)"
done
