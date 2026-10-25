REVIEWING: long-goal/aestheticcnext-m5lm7 @ 739d915a71117744d3cc02746d6d3b73fa066244
FAIL

P2 lib/auth/request-memo.ts:61 — A managed staff member opening the dashboard on an iPad still incurs 12 auth queries, including three reads of the same user, because SSR never opens a memo scope. This misses m5lm7.1’s per-page promise — scope the complete SSR/direct-session request and add an SSR query-count regression.

VERIFIED OK

- **TM-01, TM-07; H-T1/H-T3:** Tenant and role predicates remain intact. Switching, impersonation, managed visibility and IP-block tests pass. Passing `authReq` preserves the active-business hint.
- **COM-02:** Traced impersonation, membership, visibility and logout writers; found no affected same-request re-resolution after those writes.
- **H-D0, H-B1/H-B2:** Existing resolvers are reused; the access-list function was moved unchanged. No booking-decision change identified.
- Memo checks passed for 40 concurrent scopes, nested wrappers, WeakMap isolation, lazy thenables, rejection eviction, explicit clearing and background-work isolation. Module duplication creates separate caches, not shared tenant answers.
- **H-D1–H-D5; DR-06/DR-07:** Eight selected suites passed **138 assertions**; both PostgreSQL suites passed **10 tests**. Disabling memoisation failed all four query-count tests while four behavioral controls passed. Restoring the clients probe failed its scoping assertion while the activity-index control passed.
- SSR reproduction measured **12 queries / three user reads**; wrapping the same chain in the existing scope reduced this to **five queries / one user read**, preserving clinic and visibility results.
- **H-D6/H-D7; DR-02/DR-03:** Snapshot chain and journal order are correct. Real PostgreSQL EXPLAIN selected the composite index. Its ordering matches the descending activity query; plain CREATE INDEX is transaction-compatible.
- Pool ceiling is **72 connections**, including migration clients; two additional instances total **88**. Live staging configuration confirms its separate `db-f1-micro` database, one-instance cap and no pool override.
- **H-P0/H-P1:** No new logging or external-data payload identified.
- H-M1–H-M7, H-T2, H-P2/H-P3, H-C1–H-C4, H-B3–H-B6 and H-U1–H-U3 are inapplicable: no corresponding payment, messaging, storage, booking-rule or UI change.

UNVERIFIED

- **H-U0; UI-01:** No browser timing measurement; the performance finding is demonstrated through the real PostgreSQL auth chain.
- Production migration execution and lock duration; full build and landing gates. `lib/schema.ts` is five lines above its stored ratchet cap, requiring landing-time regeneration.
- Conflicting duplicate membership rows and production bundle-duplication behavior were not runtime-tested.
- Merge-tree against the checked-in `origin/main` has no conflicts; other unlanded branches were not evaluated.
- Bead comments were unavailable because Dolt was unreachable; acceptance text supplied here was used. Checkout remains unchanged.

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P2","file_line":"lib/auth/request-memo.ts:61","text":"A managed staff member opening the dashboard on an iPad still incurs 12 auth queries, including three reads of the same user, because SSR never opens a memo scope. This misses m5lm7.1’s per-page promise — scope the complete SSR/direct-session request and add an SSR query-count regression."}],"prior":[]}
```
