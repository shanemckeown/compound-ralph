---
name: smoke-tests
description: Daily E2E smoke tests — run browse-based tests against production and report pass/fail
---

You are running the daily E2E smoke test suite against production.

## What To Do

1. Run the smoke test suite:
```bash
cd /Users/shane/Documents/GitReBase/AestheticcNext && bash tests/browse-smoke/browse-smoke-full.sh 2>&1
```

Use `dangerouslyDisableSandbox: true` — the browse tool needs unrestricted network access.

2. Parse the output for the summary line (e.g., "ALL 25 TESTS PASSED" or "X/25 passed, Y failed").

3. If ALL tests pass:
   - Write a one-liner to `Ops/SMOKE_TEST_LOG.md`: `- [YYYY-MM-DD HH:MM] ✓ 25/25 passed`

4. If ANY tests fail:
   - Write to `Ops/SMOKE_TEST_LOG.md`: `- [YYYY-MM-DD HH:MM] ✗ X/25 passed — FAILED: [list failed test names]`
   - Check the failure screenshots in the output directory
   - Create a bead for each NEW failure (don't duplicate if the same test failed yesterday):
     ```bash
     cd /Users/shane/Documents/Obsidian && bd create --title="Smoke test failure: [test name]" --description="[what failed and screenshot path]" --type=bug --priority=1
     ```

5. The log file is at `/Users/shane/Documents/Obsidian/Aestheticc/Ops/SMOKE_TEST_LOG.md`. Create it if it doesn't exist.

## Test Coverage

The suite tests 10 workflows across 25 checks:
- T1-01: Appointment lifecycle (create client → book → verify)
- T1-02: Consent form chain (create template → verify persist)
- T1-03: Inventory CRUD (create → edit → persist → delete)
- T2-04: Lead pipeline (create → verify on board)
- T2-05: Package lifecycle (create → verify in list)
- T2-06: POS checkout (add to cart → pay cash → verify)
- T2-07: Public booking page (loads)
- T3-08: Settings persistence (edit → reload → verify → restore)
- T3-09: Team management (loads with members)
- T3-10: Protocol creation (create → verify in list)

## Important

- Tests run against PRODUCTION (https://aestheti.cc)
- Test account: test@aestheti.cc
- Tests create data with timestamp suffixes (self-cleaning)
- Expected runtime: ~3-5 minutes
- If the browse tool isn't installed, report that and skip
