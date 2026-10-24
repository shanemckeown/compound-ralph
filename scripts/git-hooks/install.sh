#!/usr/bin/env bash
# Install the reference-transaction guard and the live gate export (LUCY-wqn96). Orchestrator
# only, after merge. Usage: scripts/git-hooks/install.sh [repo]   (default ~/.claude)
#
# Installs from main's COMMITTED blobs (git show HEAD:...), never the working tree, and
# refuses unless HEAD is main and the caller is the orchestrator or a human terminal (the
# hook's own identity rule, run from that blob). Copies, never symlinks:
#   <common>/hooks/reference-transaction   (the guard; also refreshes the export)
#   <common>/live/run-gate                 (the only entry point gate callers use)
# then points core.hooksPath at the ABSOLUTE common hooks dir (a relative one resolves per
# worktree, where no hooks dir exists, and the hook silently never runs there), checks every
# worktree sees that value, and exports main's gate paths to <common>/live/current.
set -euo pipefail

repo="${1:-$HOME/.claude}"
g() { git -C "$repo" --no-replace-objects "$@"; }
die() { echo "install.sh: $*; refuse" >&2; exit 1; }

common="$(g rev-parse --path-format=absolute --git-common-dir)"
hooks="$common/hooks"
live="$common/live"

head_ref="$(g symbolic-ref -q HEAD || true)"
[ "$head_ref" = refs/heads/main ] || die "HEAD of $repo is '${head_ref:-detached}', not refs/heads/main"
head="$(g rev-parse --verify -q 'HEAD^{commit}')" || die "main has no commit"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for f in reference-transaction run-gate; do
  g show "$head:scripts/git-hooks/$f" > "$tmp/$f" 2>/dev/null || die "main has no scripts/git-hooks/$f"
  [ -s "$tmp/$f" ] || die "main's scripts/git-hooks/$f is empty"
  chmod 755 "$tmp/$f"
done

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

mkdir -p "$hooks" "$live"
cp "$tmp/reference-transaction" "$hooks/reference-transaction.tmp"
mv -f "$hooks/reference-transaction.tmp" "$hooks/reference-transaction"
cp "$tmp/run-gate" "$live/run-gate.tmp"
mv -f "$live/run-gate.tmp" "$live/run-gate"
g config core.hooksPath "$hooks"

for wt in "${worktrees[@]}"; do
  [ -d "$wt" ] || continue
  seen="$(git -C "$wt" config --get core.hooksPath || true)"
  [ "$seen" = "$hooks" ] || die "worktree $wt sees core.hooksPath='$seen', not $hooks"
done

(cd "$repo" && "$hooks/reference-transaction" --export "$head") || die "export of $head failed"

echo "installed: $hooks/reference-transaction"
echo "installed: $live/run-gate"
echo "core.hooksPath: $(g config --get core.hooksPath)"
echo "live/current: $(readlink "$live/current")"
