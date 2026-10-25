REVIEWING: long-goal/aestheticcnext-m5lm7 @ 739d915a71117744d3cc02746d6d3b73fa066244
PASS
P3 lib/auth/request-memo.ts:70 — H-D1 test gap: no test pins that two different users' answers stay separate inside ONE request. Mutation run: drop userId from the `user:` key (request-memo.ts:70) and from the `owned:`/`member:` keys (active-business.ts:41,85); all 8 cases of auth-chain-query-count.test.ts stay green. A real path asks about a second user inside a scoped request: pages/api/staff/shifts.ts:192 (withActiveBusinessStudioAuth) calls userHasBusinessAccess(body.userId) → readImpersonationState(other user) → loadUserAuthState. If a later edit drops a key part, a super_admin impersonating clinic X could assign a shift to a non-member, and no test would fail. The current code is correct: every key carries every input. — Add one pg (or unit) case where a scoped request resolves the actor and then a second user id (e.g. owner calls shifts POST for a non-member staff id → 403; or call loadUserAuthState(A) then loadUserAuthState(B) inside withRequestMemo and assert distinct rows).

## Trace summary (entry point → memoised reads)

- withAuth (auth-middleware.ts:94) opens the ALS scope (`withRequestMemo`). resolveAuthenticatedUser → getServerSession → session callback (index.ts:597 loadUserAuthState, :641 resolveActiveBusiness → owned/member memo, :653 loadBusinessAuthRow). The IP step uses getUserBusinessId(userId, authReq) → readImpersonationState (memo hit) → resolveActiveBusiness (memo hit), then isBusinessIpBlocked → hasIpBlockRule plus loadUserAuthState (hit). Handler calls to getActiveBusinessId / getVisibilityFilter / getUserBusinessRole reuse the same entries.
- withBusinessStudioAuth (business-auth.ts:121): scope, then resolveUserOnce (WeakMap on req), then loadUserAuthState (hit after the session callback), getActiveBusinessId (getRequestToken WeakMap, resolveActiveBusiness hit), rejectBlockedIp, loadBusinessAuthRow (hit), and getUserBusinessRole → resolveBusinessForUser (hit).
- withActiveBusinessStudioAuth (business-auth.ts:370): the scope opens once and the inner withBusinessStudioAuth reuses it (the `getStore()` branch). resolveRequestActiveBusinessId and the inner wrapper share one resolveUserOnce result per req object, and resolveAuthenticatedUser does not read req.user, so the shared result is safe.
- getUserSession SSR (lib/auth/session.ts:22): no scope. Every memoForRequest call takes the `load()` path and each builder is awaited exactly once, so it behaves the same as main.
- Mobile JWT (resolve-user.ts:52): the full users select is not memoised. isUsersActiveBusinessInactive → resolveActiveBusiness + loadBusinessAuthRow are memoised under the wrapper's scope. In getActiveBusinessId the token is null, so it falls back to getUserBusinessId(userId, resolvedUser), as on main.

## Mutations (H-D1), all reverted (tree diffed clean against backups, `git status` clean)

1. memoForRequest ignores the store (`memo && false ? …`): 4 of 8 cases go red (6/11/9/13 round trips against ≤5). The query-count guard is real.
2. One process-global Map in withRequestMemo (cross-request sharing): the "staff in managed clinic", "impersonation … each request reads it fresh" and "IP-blocked clinic" cases go red. Cross-request caching is caught.
3. userId dropped from the user/owned/member keys: all green. This is the P3 above.

Baseline runs: auth-chain-query-count 8/8 and clients-list-and-activity-scans 2/2 pass on real Postgres. Touched node suites pass 109/109: ip-blocklist-guard, business-auth, team-utils-visibility, db-pool-config, receptionist-medical-masking, team-visibility-mode config-effect.

## Hazards

- TM-01 / H-T3: no cross-tenant path found. All ALS keys carry every input: `user:{uid}`, `business:{bid}`, `ip-block:{bid}:{ip}`, `owned:{uid}:{bid|*}`, `member:{uid}:{bid|*}`. The req-object memos (token, resolved user) are per request object. The store is created per top-level call, so concurrent requests (Cloud Run concurrency 40) each get their own Map. Promise continuations resume in the awaiter's context. If Next.js loaded two copies of the module, the scope would be invisible and lookups would go uncached, which fails safe. The clients count probe is removed, and every remaining clients query in GET /api/clients is business-scoped (pg-asserted).
- Stale-after-write inside one request: I checked every writer of impersonation, team membership, visibility mode and status that a scoped route reaches. admin/impersonate.ts (the IP check runs before the write and nothing re-resolves after it), team/visibility.ts (the role check runs before the write, the response echoes the mode), team/members/[memberId].ts (the role check runs before the soft delete), team/validate-code.ts (inserts a member and nothing re-resolves), business/index.ts POST (returns the new row directly) and auth/logout.ts (bumps sessionVersion and returns). None re-resolves after its write, and clearRequestMemo has no callers. Fire-and-forget work after the response reads the same request's memo, which is seconds old at most.
- getUserBusinessRole → resolveBusinessForUser: the owner check is the same predicate (id plus userId). The membership check is the same predicate (user, business, deletedAt IS NULL), now with a deterministic ORDER BY. The return mapping is the same (`role || null`), and neither version consults impersonation. No behaviour change.
- withAuth IP step passing authReq instead of `{activeBusinessId}`: extractActiveBusinessIdHint reads `context.user.activeBusinessId`, which equals resolvedUser.activeBusinessId. The branch is only reached when both ids are falsy. Equivalent.
- Narrowed business_profiles columns (id, userId, businessName, status, teamVisibilityMode) cover every consumer: business-auth.ts:286,327-347,469-472, the status checks, and getVisibilityFilter.
- Lazy Drizzle thenables: inside a scope `Promise.resolve(load())` adopts the builder once. Outside a scope, every caller awaits the builder once. Rejected loads are evicted, and only if the entry is still the same promise. Concurrent identical lookups share one in-flight promise.
- Impersonation, business switch, staff, managed visibility, IP block and the suspended-selected-clinic fallback are pinned on real Postgres. sessionVersion and deleted/anonymised checks keep the same columns and logic (index.ts:600-617).
- H-D7 / DR-03: 0373 follows main's head 0372 (origin/main 2017be0222). The journal `when` values are monotonic. The snapshot is named with its tag, and its prevId equals 0372's id. Apart from the new index the snapshot is identical to 0372. The migration only adds an index (IF NOT EXISTS, non-concurrent, ~15k rows). The pg test's EXPLAIN shows the planner using the new index for `business_id = ? ORDER BY created_at DESC LIMIT 10`.
- Pool (DR-08): budget 80 at 9 instances gives a pool of 7, plus 1 migration client, so 8 per instance and 72 at the cap. 72 is within 90, and 88 at 11 instances is within 97. The voice services hold no DATABASE_URL. idle_timeout 20 means connections open on demand, not up to the pool maximum. No monthly cost.
- H-D0: no existing per-request cache. The only other AsyncLocalStorage (lib/utils/pre-write-log.ts) is an action-executor flag. business-access-list.ts is a verbatim move.
- H-D2: both pg suites drive the real routes and real SQL. Only cookie decoding is mocked.
- H-D4: the pg-tests project is not quarantined; both suites ran.
- Ratchet: lib/schema.ts is 17753 lines against a limit of 17748, disclosed in the commit message for the lander's regen. All other touched files are at or under their limits. The new files are 141 and 100 lines.
- Not applicable: H-M*, H-C*, H-B*, H-U*, H-P*.

## VERIFIED OK
- No cross-user or cross-clinic reuse of a memo entry, by key construction and per-request ALS scope.
- Cross-request caching is impossible, and mutation 2 shows the tests catch it.
- The ≤5 round-trip acceptance is met (4 or 5 auth queries measured) and non-vacuous (mutation 1).
- SSR and mobile JWT paths behave the same as main.
- Migration chain, snapshot and index use.
- Pool arithmetic.

## UNVERIFIED
- Staging Cloud SQL max_connections. The brief says db-f1-micro, typically 25. Staging now uses the default pool of 7+1 per instance. With idle_timeout 20 this only matters if three or more staging instances saturate at once. I did not query staging.
- I did not drive a real browser session against `npm run dev` (Next.js bundling of the ALS module was reasoned about, not observed).
- I did not inspect long-lived resources created inside a request scope that keep its store alive. For example, the first postgres-js socket inherits the store; I reasoned this costs memory only and changes no behaviour.

```review-verdict
{"verdict": "PASS", "findings": [{"severity": "P3", "file_line": "lib/auth/request-memo.ts:70", "text": "H-D1 test gap: dropping userId from the user/owned/member memo keys leaves every test green, although a real path (pages/api/staff/shifts.ts:192 userHasBusinessAccess(body.userId) inside withActiveBusinessStudioAuth) resolves a second user in one request — add a case that resolves two users in one scoped request and asserts distinct answers"}], "prior": []}
```
