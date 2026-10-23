REVIEWING: goal/aestheticcnext-q41ar @ d60331cb15581e5dc1ff7f6551f050e10e6c5550

# q41ar r9: independent code review (Astra xhigh + fresh Sonnet 5.5)

- Bead: AestheticcNext-q41ar (WhatsApp direct to Meta, Omorphia). Range reviewed: 7d9980d7f47f15395bb3be8edd21f186c89b16f4..d60331cb15581e5dc1ff7f6551f050e10e6c5550 (r7 16b8d38ced, r8 b79480425b, r9 5d30e0d9af + d60331cb15). Whole branch vs origin/main checked for regressions and merge conflicts.
- Astra `gpt-6-astra` xhigh, read-only, session 01a1652e-cb01-7831-9eca-36bccd18f6ea: FAIL, one P2. Fresh Sonnet 5.5 (Agent): PASS, one P3. Neither saw the other's output; both echoed the REVIEWING line correctly.
- claude_only_guard route: astra (not a Claude-only bead).

Verdict: FAIL

FAIL
P2 pages/api/clients/[id].ts:510 — Kelly at Omorphia, on the clinic iPad, presses Delete Client on a client who chatted with the concierge, trusting the r7 change note ("This works the same from the client delete in Aestheticc and from the API"); the in-app delete is a soft delete (deletedAt + portal sessions + photos), returns 204, and the client's Instagram and WhatsApp concierge chats stay in Front Desk; only GDPR hard delete and the OAuth v1 DELETE run eraseClientMetaConcierge — correct the r7 change note so it promises chat erasure only for GDPR erasure and the API delete, and file whether the in-app soft delete should erase concierge chats as a pre-existing follow-up (the handler is unchanged vs the merge base) (H-U0, UI-01, UI-07; Astra)
P3 lib/concierge/erase-client-meta-concierge.ts:37 — Kelly erases a client who DM'd on Instagram; the thread goes but the instagram_conversation_state row for that igsid (last inbound time, age attestation) stays, because only the clinic-wide admin delete clears it — also delete the state rows for the igsids of the removed threads (H-P3, pseudonymous id, not user-facing; Sonnet)
VERIFIED OK

- **r6 P1 closed.** Deleted-twin thread (client_id NULL) is erased through both GDPR hard delete and OAuth client DELETE by the single `eraseClientMetaConcierge`. The old WhatsApp-only eraser file is deleted and both callers switched (H-D0 clean). Family phone (another live client on the exact E.164) keeps its chat. Recycled number (thread linked to another live client) survives (r8). All new queries are business-scoped in SQL (H-T3).
- **Tokens.** `ERASED_SOCIAL_ACCOUNT_TOKENS` is spread into every Meta disconnect writer: IG/FB Settings DELETE, WhatsApp clinic disconnect (token read under row lock first so unsubscribe still works), PARTNER_REMOVED/OFFBOARDED, number swap, and the admin delete. Admin delete erases the ad_accounts token with `''` (column NOT NULL, no migration), marks it disconnected, platform + business scoped; `whatsapp_messages` are kept, per orchestrator decision f3qy1 (pg-asserted).
- **Admin endpoint.** `withAuth` requireAdmin = role super_admin (403 otherwise); businessId validated; confirm must be literal true; audit row to action_audit inside the transaction. Readers that decrypt an erased `''` ad token (activate, geo-search, pause) throw before any state change, so they fail closed.
- **Refuse-while-spending (d60331cb15).** Failed pause or any active/activating campaign refuses with 409 and erases nothing; re-checked inside the erase transaction. Both reviewers' mutants confirm the cell is non-vacuous.
- **Non-vacuity.** Astra 12/12 mutants caught (phone erasure, family/recycled guards, IG inclusion, token removal, tenant scope, campaign refusal, admin auth, confirm). Sonnet independently reverted the r8 client_id guard (recycled cell red on both paths) and the in-tx assertNoLiveCampaigns (refusal cell red).
- **Astra's P2, my own check.** `pages/api/clients/[id].ts` has no diff vs the merge base eb5a1f1453; handleDelete is a soft delete and never called the WhatsApp eraser on main either. The Delete Client dialog (components/clients/delete-client-button.tsx:95-101) promises only "deletion of the client. This action cannot be undone", nothing about chats. So the code behaviour is pre-existing and identical to main; the false promise is the sentence in the r7 change note. No code change is needed on this branch to fix the note; whether soft delete should erase concierge chats is a pre-existing product question for a follow-up (sibling of 0ynq6). Recorded as the reviewer graded it; the review lead decides blocking.
- **p7bta (activation vs admin delete race).** Both reviewers and I agree: real by source trace, money consequence high once ads run, but non-blocking today. Trigger is a super-admin API call with no UI caller, inside milliseconds of a Launch tap; prod has 0 ad_accounts and 0 managed_campaigns; Growth Engine unreachable until the Meta ads review. I agree with the lead that it fails the clinic-reality test today. It must land before the first ad account connects. Astra notes millisecond timing alone would not justify dismissal; the dormant-ads premise is what makes it non-blocking.
- **1tpf4 (409 "Nothing was deleted" after WhatsApp already disconnected).** Confirmed by both reviewers; low severity, non-blocking: internal super-admin API, no clinic user sees it, retry converges.
- **sezkh (main's token sweep).** origin/main `lib/instagram/token-sweep.ts:41-48` selects only `disconnectedAt IS NULL` rows, so an erased row is not refreshed in the ordinary case; the in-flight race stays the filed low-severity follow-up.
- **Tests I ran (scratch worktree at the tip; trunk untouched):**
  - Every node/dom suite importing a range-changed file (22 suites): 22/22 passed, 542 tests.
  - Erasure pg suites, verbose: whatsapp-concierge-erasure 10/10 (IG thread, deleted-twin unlinked thread, family phone kept, own thread only, recycled number kept; each on GDPR and OAuth paths), meta-data-erase 3/3 (IG disconnect erases only that row, refuse while spending, admin preview then delete one clinic only).
  - Related pg suites: whatsapp-meta-connection, action-writes, voice-call-logs-erasure-export, voice-taken-messages, plus the two above: 6/6 suites, 79/79 tests.
  - A mistyped jest pattern also ran the whole pg project once (not intended; the lane belongs to landing): 2983 passed, 28 skipped, 3 failed, all 3 in consent-signed-at-readers.test.ts as postgres connection timeouts under load; that file re-run alone passed 3/3. Not branch-related; still not a substitute for the landing lane on the combined tree.
  - `npm run typecheck` (tsc --noEmit, 6 GB heap): exit 0.
  - `npm run build` (8 GB heap): compiled successfully in 101s, then `spawn EBADF` at static page generation, the known local environment issue.
- **Merge-tree vs origin/main (96ed3d5819):** conflicts in `drizzle/meta/_journal.json`, `pages/api/meta/accounts/index.ts`, `tests/config-effect/coverage.config-effect.test.ts`. Journal is expected (lander renumbers); the accounts/index.ts conflict needs care at landing because r7 touches the disconnect writers next door.

UNVERIFIED

- Live Meta Embedded Signup, unsubscribe, Graph pause on a stale token, delivery and coexistence on prod (H-C4). Meta calls are mocked in tests.
- Prod premise of 0 ad_accounts / 0 managed_campaigns was supplied by the builder, not re-queried here.
- `tests/integration/gdpr/anonymise-don't-cascade.test.ts` imports a changed module but did not run in either project from my invocations.
- Next build static generation (EBADF locally); the builder's r7 evidence records exit 0 with an 8 GB heap.
- Combined landing tree with the other unlanded branches; full pg lane on that tree.
- Lander-owned: file-length ratchet, migration numbering, CHANGELOG/VERSION, schema-baseline (red on main).
