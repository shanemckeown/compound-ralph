# jev-stop-verify (LUCY-wow7r)

A Stop hook for **dispatched `/goal` workers only**. When a worker has changed code
and no check has passed since, and its closing message presents the work as done,
the stop is refused and the worker is told to run the tests and report the real
result.

- **The judgement** is jev-belay (`vendor/belay.mjs`, MIT, pinned `ef719db`,
  unmodified). It asks Jev four questions about the final message.
- **`hook.mjs` adds the Aestheticc rules:**
  - Background workers only: the session id must be in `Aestheticc/Ops/FLEET_MANIFEST.jsonl`
    and the newest host-stamped transcript record must be `sessionKind: "bg"`.
  - Only code changes count. Evidence and memory writes, CHANGELOG/VERSION edits,
    version bumps, and throwaway runners (at the worktree root, in `.claude/`,
    `.goal/`, `~/.claude/jobs/`, `tmp-*`) do not.
  - The runners workers really use count as checks: `bash qa-round-1.sh`,
    `.verify.sh`, `gated-test-run.py`, `node scripts/check-*.js`.
  - The `/goal <bead>` command line counts as the turn start. belay skips slash
    commands, so it would never fire on a worker by itself.
  - **Privacy (Shane, 26 Sep: no patient data to TypeSafe), private by construction.**
    Only four things are sent: a fixed task sentence, the worker's closing message
    passed through an **allow-list vocabulary** (`vocab.json`), the file-change count,
    and check runner names through the same filter. The vocabulary is common lowercase
    words from the system dictionary's lowercase entries (so no proper names) that are
    frequent in real worker messages, minus clinical and personal vocabulary
    (`eval/build-vocab.mjs`). Every other token becomes "…": names, numbers, ids,
    dates, emails, file names. Emails, phone numbers, NHS-shaped numbers, dates of
    birth, postcodes, honorifics or the word "patient" also skip the call entirely,
    as a second layer. Tool output is never read into the request.
    **Residual:** a patient name that is also a common lowercase English word ("rose")
    written in lowercase. It cannot be linked to anything, because no other identifier
    survives.
  - Key from keychain `jev-api-key`. Model pinned `jev-1.13.0`. Threshold 0.8.
  - At most 3 blocks per session, one per change set, 60 s apart.
  - **Fails open:** a missing key, any HTTP failure (even one later retried to
    success), a timeout, an odd answer shape, unwritable cap state, or any error
    lets the stop through. There is a 20 s decision deadline plus a 22 s process
    watchdog, and the host kills the hook at 25 s.

## Evidence (26-27 Sep 2026)
- Replayed **2,574 real stops** from 905 worker sessions (`eval/stops.mjs`, `eval/live.mjs`).
  28 reached Jev (about 1%). **0 false blocks.**
- **One true positive exists in history:** a CI script edited after its last run, then
  reported done. Jev caught it on raw text, but on vocab-redacted text it is too unsure to
  block. **The hook currently catches it only in the synthetic corpus** (2 of 2 belay
  positives still block). That is the price of private-by-construction.
- No clear pattern of false "done" in fleet history. This is cheap insurance, not a
  fix for a frequent failure.
- Cost is about $0.00005 per call. Astra xhigh reviewed it over 16 rounds (all fixes are
  tests in `test/hook.test.mjs`).

## Run
    node --test ~/.claude/hooks/jev-stop-verify/test/hook.test.mjs   # offline, no key needed
    node eval/stops.mjs 300 && node eval/live.mjs          # re-measure on real stops (uses the key)

Decisions are logged to `decisions.log` (local only).

## Enable
Add this to the `Stop` array in `~/.claude/settings.json`:

    { "hooks": [ { "type": "command", "command": "node /Users/shane/.claude/hooks/jev-stop-verify/hook.mjs", "timeout": 25 } ] }
