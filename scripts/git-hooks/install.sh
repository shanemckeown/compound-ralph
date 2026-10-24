#!/usr/bin/env bash
# Install the reference-transaction guard (LUCY-wqn96). Orchestrator only, after merge.
# Usage: scripts/git-hooks/install.sh [repo]   (default ~/.claude)
#
# Copies (never symlinks: a symlink would run uncommitted working-tree edits) the hook
# into the repo's absolute common hooks dir and points core.hooksPath at that absolute
# path. A relative core.hooksPath (e.g. ".git/hooks") resolves against each linked
# worktree's own root, where no hooks dir exists, so the hook silently never runs there.
set -euo pipefail

repo="${1:-$HOME/.claude}"
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/reference-transaction"
common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)"
hooks="$common/hooks"

mkdir -p "$hooks"
cp "$src" "$hooks/reference-transaction.tmp"
chmod 755 "$hooks/reference-transaction.tmp"
mv -f "$hooks/reference-transaction.tmp" "$hooks/reference-transaction"
git -C "$repo" config core.hooksPath "$hooks"

echo "installed: $hooks/reference-transaction"
echo "core.hooksPath: $(git -C "$repo" config --get core.hooksPath)"
