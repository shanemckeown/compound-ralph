#!/usr/bin/env bash
# lucy-closeout-commit.sh — serialised commit+push for /closeout fragments.
#
# Why this exists: 20 parallel Claude sessions all firing /closeout at once used
# to race on `git add/commit/push` in the vault. A mkdir-based lock (atomic on
# macOS, where flock(1) does not exist) serialises them. Each session holds the
# lock for ~1s, so a 20-session fan-out clears in well under a minute.
#
# Usage: lucy-closeout-commit.sh "<commit subject>" <path> [<path> ...]
#
# Only the paths you name are staged. Never `git add -A` — the vault routinely
# carries hundreds of unrelated dirty paths (deleted .beads/dolt-remote objects).

set -uo pipefail

VAULT="/Users/shane/Documents/Obsidian"
LOCK="$VAULT/.closeout.lock"
# Sizing note: the lock is held across commit AND push (~5s, mostly network). A 20-session
# fan-out therefore serialises into ~100s of waiting for the last session in the queue, so the
# timeout must comfortably exceed that. 60s was too tight and would have silently dropped the
# tail of a large fan-out — the exact case this is built for.
LOCK_TIMEOUT=300  # seconds to wait for the lock before giving up
STALE_AFTER=120   # seconds after which a held lock is presumed abandoned (max hold is ~5s)

if [ $# -lt 2 ]; then
  echo "usage: $(basename "$0") \"<commit subject>\" <path> [<path> ...]" >&2
  exit 2
fi

SUBJECT="$1"; shift
PATHS=("$@")

cd "$VAULT" || { echo "closeout-commit: cannot cd to $VAULT" >&2; exit 1; }

# ---- acquire lock -----------------------------------------------------------
acquired=0
waited=0
while [ "$waited" -lt "$LOCK_TIMEOUT" ]; do
  if mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"
    acquired=1
    break
  fi
  # Reap an abandoned lock (a session that was killed mid-commit).
  if [ -d "$LOCK" ]; then
    lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || date +%s) ))
    if [ "$lock_age" -gt "$STALE_AFTER" ]; then
      echo "closeout-commit: reaping stale lock (${lock_age}s old)" >&2
      rm -rf "$LOCK"
      continue
    fi
  fi
  sleep 1
  waited=$(( waited + 1 ))
done

if [ "$acquired" -ne 1 ]; then
  echo "closeout-commit: could not acquire lock after ${LOCK_TIMEOUT}s. Fragment is written to disk and will be committed by the next closeout or /rollup." >&2
  exit 3
fi

cleanup() { rm -rf "$LOCK"; }
trap cleanup EXIT INT TERM

# ---- stage + commit ---------------------------------------------------------
existing=()
for p in "${PATHS[@]}"; do
  if [ -e "$p" ]; then existing+=("$p"); else echo "closeout-commit: skipping missing path $p" >&2; fi
done

if [ ${#existing[@]} -eq 0 ]; then
  echo "closeout-commit: nothing to stage" >&2
  exit 4
fi

git add -- "${existing[@]}" || { echo "closeout-commit: git add failed" >&2; exit 1; }

if git diff --cached --quiet; then
  echo "closeout-commit: no staged changes (already committed?) — nothing to do"
  exit 0
fi

git commit -q -m "$SUBJECT" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>" \
  || { echo "closeout-commit: git commit failed" >&2; exit 1; }

COMMIT=$(git rev-parse --short HEAD)
echo "closeout-commit: committed $COMMIT"

# ---- push, with one rebase retry -------------------------------------------
# Fragments are new files nobody else touches, so a rebase can never conflict on
# them. We deliberately do NOT use --autostash: `git stash` is shared across
# worktrees and the vault tree is routinely dirty. If rebase can't run cleanly we
# leave the commit local — the work is durable either way and the next push takes it.
BRANCH=$(git branch --show-current)

if git push -q origin "$BRANCH" 2>/dev/null; then
  echo "closeout-commit: pushed $COMMIT to origin/$BRANCH"
  exit 0
fi

echo "closeout-commit: push rejected, attempting rebase onto origin/$BRANCH" >&2
git fetch -q origin "$BRANCH" || { echo "closeout-commit: fetch failed; commit $COMMIT held locally" >&2; exit 0; }

if git rebase -q "origin/$BRANCH" 2>/dev/null; then
  if git push -q origin "$BRANCH" 2>/dev/null; then
    echo "closeout-commit: pushed $(git rev-parse --short HEAD) after rebase"
    exit 0
  fi
  echo "closeout-commit: push still failing; commit held locally (safe — next closeout will carry it)" >&2
  exit 0
fi

git rebase --abort 2>/dev/null
echo "closeout-commit: rebase not possible (dirty tree); commit $COMMIT held locally and will push next time" >&2
exit 0
