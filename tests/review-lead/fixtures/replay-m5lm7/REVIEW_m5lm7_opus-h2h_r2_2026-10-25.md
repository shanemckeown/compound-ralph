REVIEWING: long-goal/aestheticcnext-m5lm7 @ a2e368e855ed00490bc3e701cd6f4c6c70ea1465
PASS

No new findings in round 2.

## Code paths traced (round-2 delta)

- Dashboard SSR: `pages/dashboard/*` getServerSideProps -> `getUserSession` (lib/auth/session.ts:19-26) -> `enterRequestMemo()` runs synchronously before the first await, so the store is set on the page's current async resource. Every later await in that getServerSideProps (getServerSession -> NextAuth session callback -> `loadUserAuthState` / `loadBusinessAuthRow`; `isSessionIpBlocked` -> `isBusinessIpBlocked` -> `hasIpBlockRule` / `loadUserAuthState`; then `getUserBusinessId`, `getVisibilityFilter`, `getUserBusinessRole` -> `resolveBusinessForUser` -> memoised owned/member lookups) reads the same Map. `lib/auth/require-ai-entitlement-ssr.ts:13` and `lib/marketing/studio-entry-redirect.ts:9` are only imported from dashboard SSR pages (content-studio, quick-post, campaign-capture, content/compose, business-studio/voice-marketing), so they behave the same way.
- API routes: no API route calls `getUserSession` (grep over pages/ and lib/: only the 19 dashboard pages plus the two SSR helpers). API routes keep using `withRequestMemo`'s `requestScope.run`. Nothing changes for them; if one ever did call `getUserSession` inside a withAuth scope, `enterRequestMemo` sees the existing store and does nothing.
- Writes followed by re-resolution inside SSR: none of the 19 getServerSideProps functions or the two helpers write impersonation, membership, business status or visibility (grep for db.update/insert/delete returned nothing). So the "write then stale re-read" hazard does not open up on the new SSR scope.

## enterWith runtime semantics (scratch scripts in /tmp/claude-502/m5lm7-a2e368e/opus-scratch/)

Node versions used: /usr/local/bin/node v20.14.0 (prod is node:20-bookworm-slim), v22.22.0 (nvm), and the default v25.6.1 (AsyncContextFrame). `npx -y node@20` was not needed because a local Node 20 was present.

`als-http.js` is a real http server with keep-alive. It runs awaits, then a "gSSP" that calls an async getUserSession whose `enterWith` comes before its first await. It then makes lazily created shared objects: a socket "db" connection, a module-level promise and a setInterval. Sequential requests run on one keep-alive socket, then three concurrent requests on a pool. I ran it with and without another ALS doing `run()` per request (the way Sentry's OTel context manager does).
- Every request's own continuations (after the db socket round trip, after awaiting the shared promise created by another request) saw only its own store, on all three Node versions. No request saw another request's store at its start.
- Callbacks fired by a setInterval created inside request A saw A's store when they ran on behalf of B to E. This is ordinary ALS resource propagation, and `requestScope.run` (the API path, since round 1) behaves the same way, so it is not specific to enterWith. No memoised resolver is ever called from a callback of a shared timer, emitter or socket. Postgres results reach callers through promise resolution, and the continuation runs in the awaiting request's context, which the socket test shows.
- Node 20/22 only, and only with no other ALS enabled: the first-ever `enterWith` landed on the top-level resource, and the main-module continuation saw A's store afterwards. Request chains (B, C, D, E) still did not see it. With any other ALS `run()` active (Sentry.init in instrumentation.ts registers one in prod), the top-level resource stayed clean. Node 25 never leaked.
`als-coldstart.js` covers the cold start: requests A and B are both in flight before any ALS is enabled, and B resumes after A has entered. B did not see A's store (null before, its own after) on Node 20 and Node 25, with or without a Sentry-like run().

Conclusion: a handler-level `requestScope.run` wrapper is not required for correctness. With `enterWith`, the scope also covers Next's own code after `await getServerSideProps()` in that same request (render, response). That code makes no auth lookups, and the Map is garbage once the request chain ends.

## VERIFIED OK
- Round-1 P2 (SSR never opened a scope): I ran tests/pg-integration/auth-chain-query-count.test.ts at the tip (real Postgres through testcontainers/colima) and all 10 tests passed. Removing `enterRequestMemo();` from lib/auth/session.ts makes only "SSR: a managed staff member's dashboard auth reads each row once" fail. Restored, and `cmp` confirmed it byte-identical.
- Round-1 P3 (two users in one request): changing the key `user:${userId}` to `user:` makes only "one request asking about two users gets each user's own answer" fail. Restored, and `cmp` confirmed it byte-identical. The cell covers both `loadUserAuthState` (ADMIN impersonating vs OWNER) and `resolveBusinessForUser` (owner vs staff role) in one scope (DR-06 non-vacuous, TM-01).
- The test's `ssrRequest = Promise.resolve().then(page)` puts the enterWith in its own microtask resource, so the outer test continuation (registered before `page` runs) and later tests in the file never inherit the store. The later "suspended selected clinic" cell stays green after it.
- Jest suites that call the real `getUserSession`: only tests/unit/security-ip/ssr-session.test.ts (every resolver there is mocked, so memo state cannot affect assertions) and tests/meta/sentry-bearer-route-deny.test.ts (source-text check). Every other suite mocks `@/lib/auth/session`.
- Keys carry all inputs (`user:`, `business:`, `owned:/member:<user>:<biz>`, `ip-block:<biz>:<ip>`). A failed load is evicted. `Promise.resolve(load())` adopts the lazy Drizzle thenable once (TM-01, TM-07, DR-05).
- The worktree tree was left clean (`git status --short` empty) and the node_modules symlink was removed.

## UNVERIFIED
- I did not drive an actual Next.js 15.5 SSR request end to end. The ALS behaviour comes from the http-server simulation above plus reading Next's server code, which shows no request batching for getServerSideProps pages.
- Node 20.14.0 locally versus the latest 20.x in the prod image. The ALS implementation did not change materially within 20.x as far as I know, but I did not test that version directly.
- I did not re-audit the round-1 scope (pool math, migration 0373, getUserBusinessRole ordering) beyond confirming the fix commit does not touch it. fix-r2.patch only changes lib/auth/request-memo.ts, lib/auth/session.ts and the pg test.

```review-verdict
{"verdict": "PASS", "findings": [], "prior": [{"ref": "r1 P2 lib/auth/request-memo.ts:61", "status": "closed", "evidence": "getUserSession (lib/auth/session.ts) calls enterRequestMemo() before its first await; ran auth-chain-query-count.test.ts at a2e368e855 on real Postgres: 10/10 green; mutation removing the enterRequestMemo() call turns only the SSR managed-staff cell red (1 users read, <=5 queries); restored byte-identical. Node 20.14/22/25 scratch scripts show the entered store stays within the calling request's async chain."}, {"ref": "r1 P3 lib/auth/request-memo.ts:70", "status": "closed", "evidence": "New cell 'one request asking about two users gets each user's own answer' covers loadUserAuthState and resolveBusinessForUser for two users in one scope; mutation changing key `user:${userId}` to `user:` turns only that cell red; restored byte-identical."}]}
```
