# AestheticcNext-yoi4o

## Goal
Restore useful 07:00 Slack reports and nounset-safe 07:30 triage without changing schedules or touching the live tree.

## Bead context
> Morning Slack report sends empty payload since 29 Aug — night-batch at 22:00 consumes markers before the 07:00 job runs.
>
> Secondary: morning-triage.sh has been a silent no-op for 22 days with 'line 46: beads[*]: unbound variable'.

The code confirms that morning-report previously read only active markers, archived them before delivery, and skipped Slack entirely for guarded-only results without dreams. Triage read only today's archive and expanded empty arrays unsafely on macOS Bash 3.2.

## Affected files
- `hooks/night-batch/morning-report.sh`: archive-aware morning rendering, explicit archive-only mode, useful reports without PRs.
- `hooks/night-batch/morning-triage.sh`: recent archive selection and Bash 3.2-safe arrays.
- `hooks/night-batch/morning-common.sh`: shared marker selection, dry-run guard, locking and durable Slack delivery.
- `hooks/night-batch/night-batch.sh`: explicitly invoke archive-only mode; fix existing local-assignment/quoting lint so all changed shell files pass ShellCheck.
- `hooks/night-batch/test-morning-reports.py`: isolated regression tests and rendered dry-run transcripts.
- `.gitignore`: explicitly exclude both runtime delivery trees, including their locks and DM content.
- `PLAN.md`, `VALIDATION.md`: implementation decisions and verification evidence.

## Approach
- Choose AC1's **read processed markers** option. Read yesterday's and today's local-calendar archives plus active markers, preferring active state when a bead appears in multiple places. No migration of existing marker files is required. Archived preview files stay associated with their marker directory.
- Preserve night-batch's TODO rendering and archiving. **Before:** the 22:00 job could DM an `Overnight` PR/dream summary, including the observed dream-only "0 PRs" case. **After:** it writes TODO/archive only and sends no Slack DM. The default 07:00 invocation owns that notification and includes status counts, bead IDs/titles, available PRs/previews, alerts and dreams.
- Use dated outboxes and successful-delivery receipts under state, protected by an OS file lock. Preserve pending payloads for retry; require both successful HTTP transport and Slack `ok: true` before recording success. A same-day rerun retries pending delivery or exits after an acknowledged delivery. If retry fails, finish rendering, TODO append and marker archiving, retain or queue today's payload, then exit non-zero without a second transport attempt in that run.
- An overlapping morning invocation waits for the night archive writer's OS lock, then checks receipts and archives. Lock contention cannot silently discard the scheduled morning invocation. The lock releases automatically on process exit; no stale PID lock needs manual clearing.
- Record delivered marker content identities so moving an active marker into today's archive does not announce it again tomorrow. Retain later-arriving markers for the following report. Keep recent-window selection explicit in each DM; an empty window produces an honest no-results morning DM rather than a silent exit.
- Fix every potentially empty array expansion in triage for Bash 3.2 + `set -u`, and read yesterday's archive there too. Use the same retry/delivery guard for triage's separate 07:30 notification.
- Add `--dry-run` to both scripts: render text before any credential read, curl call, TODO write, marker move or delivery receipt. Tests use only fake roots and fake `bd`, `gh`, `git`, `curl`, dates and credentials; test curl cannot use the network.
- Keep all source edits in this worktree. Test fixtures use `$TMPDIR`, falling back to the system temp directory, and clean themselves up so tests also run with the checkout read-only. Do not run real `bd`, send real Slack, push, change launchd plists or edit SYSTEM_MAP.md. The unregistered 08:45 ops-daemon morning briefing remains out of scope.

## Risk / changeover
- First morning: existing plain `processed/<yesterday>/*.{complete,reviewed,guarded}` files work immediately without receipts or new metadata. If that archive is absent, today's archive and active markers still work. If all are absent, send a dated no-results message (plus any alerts/dreams), without resurrecting historical archives.
- If the night job is skipped, active markers remain visible. Markers arriving between 22:00 and the morning snapshot join the report. Markers arriving after a successful morning snapshot are reported on the next invocation date, not as a duplicate same-day DM.
- Landing after an old night job sent its DM intentionally repeats that night's results once at 07:00; old Slack sends have no reliable receipt to import. There is no deliberate skipped changeover day. Deploy the changed scripts together: an old night-batch caller with a new default morning-report would still send at night.
- A normal retry or concurrent invocation must not duplicate a confirmed DM. There remains an unavoidable delivery ambiguity if Slack accepts a request but the response/receipt is lost; retry can duplicate that DM. Do not claim exactly-once delivery across network/process failure. Outboxes preserve failed payloads even after the archive date falls outside the recent window.
- A stuck delivery (today or an older date) is logged and returns non-zero only after the morning report finishes its local rendering, TODO append and marker archiving. The existing successful-delivery short-circuit still exits zero for today's acknowledged DM. A failed retry is not attempted twice in the same run.
- Same-day queuing keeps the first payload and marker IDs unchanged, without creating a staging directory inside the existing outbox. New active results are still appended to TODO and archived during a failed retry; they are absent from the preserved DM and remain eligible for the next day's recent-window report. This choice preserves the exact retry content.
- TODO append and marker archive are not one transaction: an interruption between them can duplicate a TODO section on retry. Slack delivery receipts are independent of that operation. Archive failures must stop the run visibly rather than silently dropping markers.
- Marker identity uses basename plus content; identical content for the same bead/status is treated as the same result. Existing markers contain run timestamps. Dates follow the job's local timezone, with calendar-day subtraction across DST.
- Missing/invalid credentials or Slack errors leave an unsent outbox and return failure. No scheduling change is required; launchd execution and actual delivery can only be verified after the dispatching session lands the commits.
- Successful Slack responses now live in `state/<morning-report|morning-triage>-delivery/<date>/sent.json` beside `payload.json` and marker identities, rather than the old overwritten `shane-gate-dm.log` / `morning-triage-dm.log`. The usual script logs record acknowledgement or failure and the outbox path.
- Both delivery trees are explicitly gitignored, including `.lock`, payloads and responses containing DM text. Existing broad state ignores remain in place; tracked `state/processed/` history remains tracked and unchanged.

## Rollback
Revert the implementation commit as a unit. Keep runtime archives and delivery state intact; do not delete marker history. Reverting restores the known morning-report bug and the old night DM behavior. No plist rollback is needed.

## Acceptance criteria (pass/fail)
- Archived-only legacy fixtures render the correct complete/reviewed/guarded counts and guarded bead details at 07:00, including without any PR or dream. Missing-yesterday, skipped-night, after-midnight and late-active fixtures retain real results.
- Default morning delivery is acknowledged once per date; same-day retry and concurrent calls do not repeat success. Failed HTTP/API responses preserve pending payloads, and later retries deliver them. Moving a delivered marker does not announce it again tomorrow.
- Failed same-day and older-backlog retries still append fresh active results to TODO and archive them, log failure and exit non-zero. Same-day retries preserve the original payload/marker IDs, deliver one acknowledged DM after recovery and leave no orphaned staging directory.
- Night archive-only mode writes TODO and archives markers without invoking Slack; the following morning still renders those results. Existing archived-only results do not get appended to TODO again.
- Triage works with zero, one and multiple beads and empty diagnostic arrays under `/bin/bash` 3.2 and the installed modern Bash. Yesterday-only markers produce a useful pipeline verdict.
- ShellCheck passes both requested scripts and the shared helper; shell syntax checks pass all changed shell files.
- Committed validation includes both fake-fixture DM texts, test results and proof that no real Slack transport or credentials were used. No live files, tracked state markers or plists change; all implementation commits reference AestheticcNext-yoi4o and remain unpushed.

## Verification result
All local criteria pass: 18 regression tests, ShellCheck 0.11.0 on all four changed shell files, syntax checks for all four under Bash 3.2 and 5.3, and read-only dry-run transcripts. See `VALIDATION.md` for commands, outputs, the exact DMs and transport isolation. Real 07:00 delivery is intentionally untested here because this worker is forbidden to send Slack or land the change.
