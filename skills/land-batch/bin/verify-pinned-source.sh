#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  printf 'usage: %s <repo> <source-ref> <expected-sha>\n' "${0##*/}" >&2
  exit 64
fi

repo="$1"
source_ref="$2"
expected_sha="$3"

actual_sha="$(git -C "$repo" rev-parse --verify --quiet "$source_ref^{commit}")" || {
  printf 'source missing: %s\n' "$source_ref" >&2
  exit 2
}

if [[ "$actual_sha" != "$expected_sha" ]]; then
  printf 'source moved: %s was %s now %s; rediscover\n' \
    "$source_ref" "$expected_sha" "$actual_sha" >&2
  exit 3
fi

printf '%s\n' "$actual_sha"
