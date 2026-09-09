# AestheticcNext-yoi4o validation — 2026-09-09

Worktree: `/Users/shane/.claude/.claude/worktrees/goal-aestheticcnext-yoi4o`

Branch: `goal/aestheticcnext-yoi4o`, starting at `95cdfd5`.

Round 2 starts at `2fc71de`. Changes address delivery failures blocking local work, explicit delivery-state ignores and temporary fixture placement. Pending same-day payloads and marker IDs stay unchanged; newly arriving active results still reach TODO/archive and remain eligible for the following day's DM.

## Non-sending proof

- Both reporters' `--dry-run` branches print the rendered DM and exit before TODO writes, marker moves, credential reads, payload creation or Slack transport. The shared transport function also refuses dry-run and archive-only mode.
- The test runner constructs a fresh environment with a fake HOME, root, TODO, worktrees, dreams, BEADS_DIR, Slack env file and user `U_FAKE_SHANE`. It does not inherit tokens. All fixture paths are temporary directories under `$TMPDIR` (or the system temp directory when unset), with automatic cleanup; the suite no longer writes fixtures into the checkout.
- `bd`, `gh`, `git`, `date` and `curl` resolve to fixture scripts. No real `bd` command was run. The fake curl contains no networking imports, socket calls, subprocess calls or fallback execution. It accepts only the literal fake token `xoxb-FAKE-NOT-A-TOKEN`, reads the payload locally, writes `curl-calls.jsonl`, and returns a fixture response.
- Dry-run tests omit `FAKE_TRANSPORT=1`, so even the fake curl rejects an accidental call. They also point the credential path at a FIFO: reading it would block and fail the test timeout. All dry-runs completed, invoked curl zero times, and left a byte-for-byte identical fixture file snapshot.
- Delivery/retry tests explicitly enable only the local fake transport; HTTP failures, Slack `ok:false`, malformed JSON and successful acknowledgements are simulated locally. No real Slack request, webhook, token or client-facing message was used.
- No launchd plists, SYSTEM_MAP.md, live hook files or tracked state markers were edited. The 08:45 unregistered briefing remains out of scope. No push, merge or deployment was performed.

## Static checks

ShellCheck [official v0.11.0 release](https://github.com/koalaman/shellcheck/releases/tag/v0.11.0), Darwin aarch64. Round 2 reused `/private/tmp/claude-502/shellcheck-v0.11.0/shellcheck` and confirmed version 0.11.0. The previously cited SHA-256 is the release archive's checksum; the extracted binary has a separate checksum. Both were re-read locally:

```text
56affdd8de5527894dca6dc3d7e0a99a873b0f004d7aabc30ae407d3f48b0a79  /private/tmp/claude-502/sc.tar.xz
61c17246d69f012cd458ae82f244c46023dac75d1b69733ca1cc7d28fb270fd7  /private/tmp/claude-502/shellcheck-v0.11.0/shellcheck
```

```console
$ /private/tmp/claude-502/shellcheck-v0.11.0/shellcheck -x hooks/night-batch/morning-report.sh hooks/night-batch/morning-triage.sh hooks/night-batch/morning-common.sh hooks/night-batch/night-batch.sh
(no diagnostics)
exit 0
```

The same command without `-x` also passed. The only inline disable is SC2016 on a literal Markdown log-path string with backticks; the comment explains why those backticks must not execute as shell substitutions.

```text
PASS: bash -n for all 4 changed shell files under Bash 3.2 and Bash 5.3 (8 checks)
ShellCheck 0.11.0 output: (no diagnostics)
ShellCheck exit: 0
git diff --check: no diagnostics, exit 0
```

## Regression tests

```console
$ python3 hooks/night-batch/test-morning-reports.py
test_complete_without_pr_still_has_details (__main__.Reports.test_complete_without_pr_still_has_details) ... ok
test_concurrent_delivery_has_one_acknowledgement (__main__.Reports.test_concurrent_delivery_has_one_acknowledgement) ... ok
test_delivery_failures_keep_exact_payload_for_retry (__main__.Reports.test_delivery_failures_keep_exact_payload_for_retry) ... ok
test_dry_runs_are_read_only_and_render_mixed_sources (__main__.Reports.test_dry_runs_are_read_only_and_render_mixed_sources) ... ok
test_failed_same_day_retry_keeps_payload_and_completes_local_work (__main__.Reports.test_failed_same_day_retry_keeps_payload_and_completes_local_work) ... ok
test_legacy_guarded_only_first_morning_and_empty_arrays (__main__.Reports.test_legacy_guarded_only_first_morning_and_empty_arrays) ... ok
test_missing_token_and_retry_outside_archive_window (__main__.Reports.test_missing_token_and_retry_outside_archive_window) ... ok
test_morning_waits_for_overlapping_night_archive (__main__.Reports.test_morning_waits_for_overlapping_night_archive) ... ok
test_night_archives_without_slack_then_morning_reports (__main__.Reports.test_night_archives_without_slack_then_morning_reports) ... ok
test_no_markers_and_historical_archives_are_not_resurrected (__main__.Reports.test_no_markers_and_historical_archives_are_not_resurrected) ... ok
test_skipped_night_and_active_precedence (__main__.Reports.test_skipped_night_and_active_precedence) ... ok
test_stuck_backlog_does_not_block_todo_or_archiving (__main__.Reports.test_stuck_backlog_does_not_block_todo_or_archiving) ... ok
test_success_receipts_and_late_marker_next_morning (__main__.Reports.test_success_receipts_and_late_marker_next_morning) ... ok
test_today_only_missing_yesterday_and_calendar_boundaries (__main__.Reports.test_today_only_missing_yesterday_and_calendar_boundaries) ... ok
test_todo_failure_does_not_consume_markers_or_send (__main__.Reports.test_todo_failure_does_not_consume_markers_or_send) ... ok
test_triage_all_diagnostic_arrays_empty (__main__.Reports.test_triage_all_diagnostic_arrays_empty) ... ok
test_triage_reports_diff_churn (__main__.Reports.test_triage_reports_diff_churn) ... ok
test_zero_eligible_and_dreams_without_markers (__main__.Reports.test_zero_eligible_and_dreams_without_markers) ... ok

----------------------------------------------------------------------
Ran 18 tests in 25.406s

OK
exit 0
```

The dry-run, empty-window, legacy-archive and empty-diagnostic-array cases execute with both `/bin/bash` 3.2.57 and `/opt/homebrew/bin/bash` 5.3.3. The source dates include spring/autumn DST boundaries and New Year. Delivery tests verify exact payload preservation, once-per-date acknowledged delivery, archival-move deduplication and waiting for an overlapping night archive operation.

New Round 2 tests:

- `test_failed_same_day_retry_keeps_payload_and_completes_local_work`: missing-token delivery failure, blank-token same-day failure with fresh marker/preview/alert, successful recovery and another same-day rerun. Asserts TODO content, archived files, unchanged payload and marker IDs, one DM with the original content, a receipt and no orphaned `.pending-*` directory. The next day's DM includes the fresh result and excludes the previously delivered result.
- `test_stuck_backlog_does_not_block_todo_or_archiving`: missing, blank and mangled fake tokens, transport failure, Slack `ok:false` and malformed JSON. Each failed prior-day retry logs failure and returns non-zero while today's fresh result reaches TODO/archive and today's pending payload; the old payload remains intact without a receipt.

The full suite ran through the agent's default sandbox without escalation. Separate fixture checks verified both an explicit temporary `$TMPDIR` override and an unset `$TMPDIR`, including cleanup:

```text
PASS: explicit TMPDIR honoured and fixture cleaned up
PASS: unset TMPDIR falls back to system temporary directory and cleans up
```

An additional nested `sandbox-exec` experiment could not start (`sandbox_apply: Operation not permitted`); it is not counted as evidence that checkout writes were denied. The normal suite and direct temporary-directory checks above passed.

Both delivery directories already matched broad state ignores; the new anchored entries make their exclusion explicit. Read-only checks confirmed ignored lock/payload/response/receipt/marker paths for both reporters and that all three tracked processed-history files remain tracked and unchanged.

## Exact dry-run DM transcripts

Reproduce both with:

```console
python3 hooks/night-batch/test-morning-reports.py --transcripts
```

Fixture: one `.complete` in yesterday's legacy archive with a ready preview and PR; one `.reviewed` in today's archive with one Codex BLOCKER and PR; one active `.guarded` with no PR. Two of three have bd-cleanup logs. All links are `example.invalid` placeholders. Dates and times are fixed by the local date stub.

```text
$ /bin/bash hooks/night-batch/morning-report.sh --dry-run [isolated fake environment]
*Morning report* — Wed 09 Sep 2026 07:00 BST
Sources: active markers + archives 2026-09-08 and 2026-09-09.

🟢 1 passed gates · 🟡 1 need review · 🔴 1 guarded

🟢 <https://example.invalid/pull/101|AestheticcNext-ready>: Fix "quoted" reminder (complete · <https://example.invalid/preview|preview>)
🟡 <https://example.invalid/pull/102|AestheticcNext-review>: Review consent checks (reviewed)
🔴 AestheticcNext-guard: Investigate oversized diff (guarded)

Review: SHANE_TODO.md → /morning-review; investigate guarded beads before shipping.
```

```text
$ /bin/bash hooks/night-batch/morning-triage.sh --dry-run [isolated fake environment]
*Overnight triage* — Wed 09 Sep 2026 07:30 BST
Health: *PARTIAL* · launchd-fired: YES · dispatched: 3/15

Sources: active markers + archives 2026-09-08 and 2026-09-09.

:large_green_circle: ready to ship: 1
:large_yellow_circle: codex flagged: 1
:red_circle: gate-2 guarded: 1

bd-cleanup ran: 2/3
staging-previews ready: 1/3
PR diff hygiene: 2/3 clean (no bd state churn)

*Codex BLOCKERs:*
• AestheticcNext-review (1)

:warning: *bd-cleanup did NOT run on:* AestheticcNext-guard

Recommendation: open `/morning-review` and review flagged beads before shipping.
```

```text
Safety: no curl calls; fake roots/credentials; bd, gh and git replaced by local stubs.
```

Actual 07:00 launchd execution and Slack delivery require landing these files together and observing the scheduled run. This worker intentionally did neither. The remaining accepted-send/lost-receipt duplicate window and first-morning changeover behavior are documented in PLAN.md.
