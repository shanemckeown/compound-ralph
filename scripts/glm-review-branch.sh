#!/usr/bin/env bash

set -u

if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 <bead-id>" >&2
  exit 1
fi

bead_id="$1"
bead_id_lower="$(printf '%s' "$bead_id" | tr '[:upper:]' '[:lower:]')"
claude_dir="${HOME}/.claude"
worker_dir="${claude_dir}/fleet/codex-workers/${bead_id}"
worker_worktree="${claude_dir}/worktrees/goal-codex-${bead_id_lower}"
review_path="${worker_dir}/review.txt"
aestheticcnext_repo="/Users/shane/Documents/GitReBase/AestheticcNext"
slot_cli="${claude_dir}/scripts/fleet-slots.py"
glm_cli="${HOME}/.local/bin/glm"

if [[ ! -d "$worker_worktree" ]]; then
  echo "Error: no worker worktree exists for bead ${bead_id}: ${worker_worktree}" >&2
  echo "Nothing to review." >&2
  exit 1
fi

if [[ ! -d "$worker_dir" ]]; then
  echo "Error: worker directory does not exist for bead ${bead_id}: ${worker_dir}" >&2
  exit 1
fi

bead_title="title unavailable"
bead_description=""
bead_acceptance_criteria=""

if bead_json="$(cd "$aestheticcnext_repo" && bd show "$bead_id" --json)"; then
  if jq -e 'type == "array" and length > 0' >/dev/null 2>&1 <<<"$bead_json"; then
    bead_title="$(jq -r '.[0].title // "title unavailable"' <<<"$bead_json")"
    bead_description="$(jq -r '.[0].description // ""' <<<"$bead_json")"
    bead_acceptance_criteria="$(jq -r '.[0].acceptance_criteria // ""' <<<"$bead_json")"
  else
    echo "Warning: bd returned no parseable issue for ${bead_id}; proceeding with a generic review prompt." >&2
  fi
else
  echo "Warning: bd lookup failed for ${bead_id}; proceeding with a generic review prompt." >&2
fi

bead_context=""
if [[ -n "$bead_description" ]]; then
  bead_context=" Description: ${bead_description}."
fi
if [[ -n "$bead_acceptance_criteria" ]]; then
  bead_context+=" Acceptance criteria: ${bead_acceptance_criteria}."
fi

focus_prompt="Adversarially review the working tree at this path against its diff vs origin/main, for bead ${bead_id}: ${bead_title}.${bead_context} Look for real bugs, correctness issues, and whether the acceptance criteria are genuinely met -- not style nits. Your response MUST start with a single line that is exactly the word PASS or the word FAIL (PASS = no real issues found and acceptance criteria are met; FAIL = at least one real issue), followed by a blank line, followed by your findings in prose."

slot_error_file="$(mktemp "${TMPDIR:-/tmp}/glm-review-slot.XXXXXX")" || {
  echo "Error: could not create a temporary file for the slot claim." >&2
  exit 1
}

slot_token="$(python3 "$slot_cli" claim-codex-glm glm "review:${bead_id}" 2>"$slot_error_file")"
claim_status=$?
if [[ "$claim_status" -ne 0 ]]; then
  slot_reason="$(<"$slot_error_file")"
  rm -f -- "$slot_error_file"
  if [[ -z "$slot_reason" ]]; then
    slot_reason="claim command exited ${claim_status}"
  fi
  echo "Error: could not claim codex_glm slot for ${bead_id}: ${slot_reason}" >&2
  exit 1
fi
rm -f -- "$slot_error_file"

slot_claimed=1
tmp_review=""
tmp_final=""

release_slot() {
  if [[ "$slot_claimed" -eq 1 ]]; then
    python3 "$slot_cli" release-codex-glm "$slot_token"
    slot_claimed=0
  fi
}

cleanup() {
  release_slot
  if [[ -n "$tmp_review" ]]; then
    rm -f -- "$tmp_review"
  fi
  if [[ -n "$tmp_final" ]]; then
    rm -f -- "$tmp_final"
  fi
}
trap cleanup EXIT

tmp_review="$(mktemp "${TMPDIR:-/tmp}/glm-review-output.XXXXXX")" || {
  echo "Error: could not create a temporary review output file." >&2
  exit 1
}

# Use a ten-minute timeout so a hung one-shot call cannot retain a slot forever.
python3 - "$glm_cli" "$focus_prompt" "$worker_worktree" "$tmp_review" <<'PY'
import subprocess
import sys

glm_cli, focus_prompt, worker_worktree, output_file = sys.argv[1:]
try:
    result = subprocess.run(
        [
            glm_cli,
            "review",
            focus_prompt,
            "--cd",
            worker_worktree,
            "--base",
            "origin/main",
            "-o",
            output_file,
        ],
        timeout=600,
        check=False,
    )
except subprocess.TimeoutExpired:
    print("glm review timed out after 600 seconds", file=sys.stderr)
    raise SystemExit(124)
except OSError as exc:
    print(f"could not start glm review: {exc}", file=sys.stderr)
    raise SystemExit(127)

raise SystemExit(result.returncode)
PY
glm_status=$?

# Release immediately after the foreground call; the EXIT trap remains as a fallback.
release_slot

if [[ "$glm_status" -ne 0 ]] || [[ ! -s "$tmp_review" ]]; then
  echo "Error: glm review call failed for ${bead_id} (exit ${glm_status}, output missing or empty)." >&2
  echo "No review.txt was written." >&2
  exit 1
fi

first_line=""
IFS= read -r first_line <"$tmp_review" || true
trimmed_first_line="$(printf '%s' "$first_line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

tmp_final="$(mktemp "${worker_dir}/.review.txt.XXXXXX")" || {
  echo "Error: could not create a temporary file in ${worker_dir}." >&2
  exit 1
}

if [[ "$trimmed_first_line" == "PASS" ]] || [[ "$trimmed_first_line" == "FAIL" ]]; then
  if ! cp -- "$tmp_review" "$tmp_final" || ! mv -f -- "$tmp_final" "$review_path"; then
    echo "Error: could not write completed review to ${review_path}." >&2
    exit 1
  fi
  tmp_final=""
  final_verdict="$trimmed_first_line"
else
  if ! {
    printf 'FAIL\n\n'
    printf 'glm did not return a parseable PASS/FAIL verdict\n\n'
    cat "$tmp_review"
  } >"$tmp_final" || ! mv -f -- "$tmp_final" "$review_path"; then
    echo "Error: could not write malformed glm response to ${review_path}." >&2
    exit 1
  fi
  tmp_final=""
  final_verdict="malformed (recorded as FAIL)"
fi

echo "Review verdict: ${final_verdict}"
echo "Review written to: ${review_path}"
