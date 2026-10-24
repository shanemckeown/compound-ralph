#!/usr/bin/env bash
# Install the reference-transaction guard (LUCY-wqn96). Orchestrator only, after merge.
# Usage: scripts/git-hooks/install.sh [repo]   (default ~/.claude)
#
# Installs from main's COMMITTED blob (git show HEAD:...), never the working tree, and
# refuses unless HEAD is main and the caller is the orchestrator or a human terminal (the
# hook's own identity rule, run from that blob). Copies (never symlinks: a symlink would run
# uncommitted working-tree edits) the hook into the repo's absolute common hooks dir, points
# core.hooksPath at that absolute path, then checks every worktree sees that value. A
# relative core.hooksPath (e.g. ".git/hooks") resolves against each linked worktree's own
# root, where no hooks dir exists, so the hook silently never runs there.
set -euo pipefail

repo="${1:-$HOME/.claude}"
g() { git -C "$repo" --no-replace-objects "$@"; }
die() { echo "install.sh: $*; refuse" >&2; exit 1; }

common="$(g rev-parse --path-format=absolute --git-common-dir)"
hooks="$common/hooks"

head_ref="$(g symbolic-ref -q HEAD || true)"
[ "$head_ref" = refs/heads/main ] || die "HEAD of $repo is '${head_ref:-detached}', not refs/heads/main"
head="$(g rev-parse --verify -q 'HEAD^{commit}')" || die "main has no commit"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
g show "$head:scripts/git-hooks/reference-transaction" > "$tmp/reference-transaction" 2>/dev/null \
  || die "main has no scripts/git-hooks/reference-transaction"
[ -s "$tmp/reference-transaction" ] || die "main's scripts/git-hooks/reference-transaction is empty"
chmod 755 "$tmp/reference-transaction"

# Only the orchestrator (or Shane at a plain terminal) installs.
(cd "$repo" && "$tmp/reference-transaction" --check-orchestrator) \
  || die "this session is not the orchestrator"

# Worktree paths read NUL-delimited (-z): a newline in a path must not truncate it.
worktrees=()
while IFS= read -r -d '' rec; do
  case "$rec" in "worktree "*) worktrees+=("${rec#worktree }") ;; esac
done < <(g worktree list --porcelain -z)
[ "${#worktrees[@]}" -gt 0 ] || die "git worktree list returned no worktrees"

# A per-worktree core.hooksPath (extensions.worktreeConfig) would override the shared one.
if [ "$(g config --type=bool --get extensions.worktreeConfig || echo false)" = true ]; then
  for wt in "${worktrees[@]}"; do
    [ -d "$wt" ] || continue
    own="$(git -C "$wt" config --worktree --get core.hooksPath || true)"
    if [ -n "$own" ] && [ "$own" != "$hooks" ]; then
      die "worktree $wt sets its own core.hooksPath=$own (unset it with: git -C '$wt' config --worktree --unset core.hooksPath)"
    fi
  done
fi

mkdir -p "$hooks"
cp "$tmp/reference-transaction" "$hooks/reference-transaction.tmp"
mv -f "$hooks/reference-transaction.tmp" "$hooks/reference-transaction"
g config core.hooksPath "$hooks"

for wt in "${worktrees[@]}"; do
  [ -d "$wt" ] || continue
  seen="$(git -C "$wt" config --get core.hooksPath || true)"
  [ "$seen" = "$hooks" ] || die "worktree $wt sees core.hooksPath='$seen', not $hooks"
done

echo "installed: $hooks/reference-transaction"
echo "core.hooksPath: $(g config --get core.hooksPath)"
