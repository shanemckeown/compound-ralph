REVIEWING: goal/aestheticcnext-q41ar @ 7649f85281ef8012cae27cd56d1a3f6cea301c2c

# q41ar r10: independent delta review (Astra xhigh + fresh Sonnet 5.5)

- Bead: AestheticcNext-q41ar. Delta d60331cb15581e5dc1ff7f6551f050e10e6c5550..7649f85281ef8012cae27cd56d1a3f6cea301c2c (one commit, 2 files: lib/concierge/erase-client-meta-concierge.ts, tests/pg-integration/whatsapp-concierge-erasure.test.ts).
- Astra `gpt-6-astra` xhigh, read-only: FAIL, one finding (graded P2 by Astra). Fresh Sonnet 5.5 (Agent): PASS, no findings. Neither saw the other's output; both echoed the REVIEWING line correctly.
- claude_only_guard route: astra.

Verdict: FAIL

FAIL
P2 lib/concierge/erase-client-meta-concierge.ts:138 — the erased person sends a new Instagram DM at the same moment their own GDPR or API erasure commits; the webhook has already refreshed the window row (lib/agent-core/gateways/instagram.ts:342), the erasure transaction deletes that freshly refreshed row, then the webhook's window check (instagram.ts:367) returns false and the reply and thread creation are skipped while the webhook still returns 200; stillUsed cannot protect a thread not created yet — serialise erasure with inbound window/thread processing per business and sender (or re-check after the window read) and add a deterministic pg interleaving test (COM-02, DR-01; Astra)

VERIFIED OK

- **Lead's note on the one finding (my own trace, not a regrade).** The race is real by source trace: recordInboundInstagramMessage, persistInstagramAgeStatement, findExistingInstagramClientId and createOrAppendLeadFromInstagramDm all run between the window upsert and isWithinInstagramWindow, with no shared lock against the eraser. Trigger needs the erased person's own DM to land within that few-statement window of their own erasure commit; no ordinary-use path found. Effect: one DM gets no auto-reply (the lead row is still written); the next DM re-opens the window. The same race class already exists before r10 for the thread delete itself (findExistingInstagramClientId can return the client being erased) and for the unchanged clinic-wide admin delete, which also deletes these rows. It touches a webhook path, so it is left to the review lead and orchestrator, graded as the reviewer graded it. Sonnet assessed the same interleaving and found no harmful clinic scenario.
- **Tenancy (H-T3, TM-01).** All three new SQL statements are business-scoped in SQL: the IG thread select (business_id + client_id + channel), the stillUsed subquery (business_id + channel), and the state delete (business_id). Unique index is (business_id, igsid). Another clinic's thread for the same igsid cannot keep this clinic's row alive, and another clinic's row cannot be deleted. Both reviewers and I agree.
- **Over-deletion.** Only igsids of the erased client's own IG threads are candidates. A remaining same-clinic IG thread with that igsid (unlinked, or linked to another client) keeps the row: the thread delete runs earlier in the same transaction, so stillUsed sees only surviving threads. `channel_identifier` and `igsid` are NOT NULL (lib/schema.ts:5507, 15874), so NOT IN has no NULL hazard.
- **Under-deletion.** Select and thread delete share the predicate (business + client + instagram); every deleted IG thread's igsid is collected first.
- **No-IG / no-phone clients.** Empty igsid list returns early; the call sits before the `if (!e164) return`, so a client with no phone still gets the IG cleanup. r8 family-phone and recycled-number WhatsApp guards are unchanged.
- **H-D0.** Extends the one per-client eraser; the clinic-wide admin delete (lib/meta/delete-clinic-meta-data.ts) is a different scope and untouched. No duplicate path.
- **H-P3.** Both hard-erasure paths (executeHardDeletion at lib/services/gdpr-deletion.ts:642, hardDeleteClient at lib/clients/hard-delete-client.ts:54) run the cleanup inside their transaction, before the client row goes.
- **H-D1 non-vacuity, run by me in a scratch worktree (trunk untouched):** reverting the new eraseInstagramWindowState call turns the IG cell red on both the GDPR and OAuth paths (2 failed, 8 passed); dropping the business filter on the state delete also turns it red (2 failed, 8 passed); restored and green. Builder's 5/5 mutants (call removed, delete not business-scoped, not limited to erased igsids, stillUsed removed, stillUsed not business-scoped) agree; both reviewers confirmed each by fixture analysis.
- **H-D2.** The cell drives the real executeHardDeletion and hardDeleteClient on real Postgres via describe.each over both paths; suite is in pg-tests and not quarantined.
- **Tests I ran at the tip:**
  - tests/pg-integration/whatsapp-concierge-erasure.test.ts alone, verbose: 10/10 (IG cell, deleted-twin, family phone, own thread only, recycled number; each on GDPR and OAuth).
  - targeted-tests.py over the delta: node 6 suites 199/199 (gdpr-delete route, gdpr-deletion, cron-auth, client-documents GDPR, v1 client contact responses, v1 clients delete); pg 5 suites 87 passed, 28 skipped (whatsapp-concierge-erasure, portal-client-accounts-tenancy, action-writes, voice-taken-messages, voice-call-logs-erasure-export).
  - `tsc --noEmit` (6 GB heap, non-incremental): exit 0.
  - A mistyped first jest invocation ran the whole pg project once (not intended): 2956 passed, 30 failed in 5 suites incl. quickbooks-sync and portal-client-accounts-tenancy under full-lane load; portal-client-accounts-tenancy passed alone in the targeted run. Not used as evidence either way.
- **Merge-tree vs origin/main (96ed3d5819):** conflicts in drizzle/meta/_journal.json (expected, lander renumbers), pages/api/meta/accounts/index.ts and tests/config-effect/coverage.config-effect.test.ts, unchanged from r9; r10 adds no new conflict.

UNVERIFIED

- The concurrent webhook/erasure interleaving was traced, not executed.
- Live Instagram webhook delivery on prod (H-C4); Meta calls are mocked in tests.
- Full pg lane and next build on the combined landing tree (lander-owned), plus file-length ratchet, migration numbering, CHANGELOG/VERSION and schema-baseline.
- Sonnet skimmed the personas rather than reading them in full; Astra cited COM-02 and DR-01.
