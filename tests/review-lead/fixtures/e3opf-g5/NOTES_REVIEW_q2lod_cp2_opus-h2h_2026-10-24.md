REVIEWING: long-goal/aestheticcnext-q2lod @ c93acdce685da213ef31b84888a430a05f893d8b
PASS
AestheticcNext-q2lod: PASS

Reviewer: fresh-context Opus 5.5 head-to-head (independent seat), whole diff 8c21d297c6..c93acdce68, depth on everything after the reviewed pin 8ee20401f7 (q2lod.18, q2lod.19, q2lod.11, q2lod.13, and the cp1 P1 login-vs-reset fence). Blind to the Astra cp2 seat. No source edited; one mutation run in the scratch tree, restored with cp (verified by grep).

Findings (new at this tip): none at P1/P2. No new P3 raised.

Open P3s carried from earlier rounds (non-blocking, none user-facing in ordinary use):
- P3 lib/portal/client-accounts/tokens.ts:172 + :206-241 — A client's app refresh hits a cold Cloud Run instance, the 15 s deadline fires, the retry wins rotation on a warm instance, then the delayed original revokes the successor; she is asked for her password once. Unchanged at this tip; earlier P3 grading kept. Fix later: a retry marker on the request, or idempotent rotation.
- P3 mobile-portal/src/services/account-session.ts:121-126 — A client swipes the app away mid-refresh and opens it next day; the 30 s grace has passed, so she types her password instead of Face ID. Design limit of the grace; unchanged.
- P3 tests/integration/portal/data-export.test.ts:127 — test weakness only: JSON.stringify of logger calls drops nested Error.message, so a regression that logs a raw Error carrying the client's email would pass. Current handler is safe (unchanged since 8ee20401f7). Fix: assert exact logger metadata.
- P3 lib/portal/client-accounts/reset-email.ts:51 (now the anyLinkedClinicSendable kind check) — a client whose every linked clinic has the master comms pause on and who forgot her password cannot get the deletion email; the password path still works. Manager-accepted default.
- P3 tests/pg-integration/portal-client-accounts-delete-reauth.test.ts:207 — "deletes once" is an equivalent mutant (account delete cascades the token). No user impact; accepted.
- P3 ACCEPTED (Shane, 24 Oct, q2lod.7.1): mobile-portal/src/services/account-session.ts:221 — delayed clinic-A selection adopted after a clinic-B link opens; she sees only her own records under the wrong clinic label.
- P3 ACCEPTED (Shane, 24 Oct, q2lod.7.1): mobile-portal/src/app/PortalGate.tsx:168 — late clinic-A config callback over a clinic-B link cookie; same impact as above.

VERIFIED OK
New work at this tip (traced entry point to write):
- cp1 P1 fix: passwordLogin (lib/portal/client-accounts/account-sessions.ts:64-112) hashes outside the lock, then under lockAccount (SELECT ... FOR UPDATE on client_accounts) re-reads password_hash/disabled_at and issues the account token and refresh token in the same tx. completeReset (enrolment.ts:118-140) UPDATEs the same row (takes the same row lock) and revokes inside its tx, so in READ COMMITTED the login's post-lock SELECT sees the committed new hash and refuses. tests/pg-integration/portal-client-accounts-login-reset-fence.test.ts 3/3 green; MUTATION: replacing the hash re-check with `false` turns "a login paused after its hash compare, overtaken by a reset, is refused" red (1 failed / 2 passed), restored and re-checked. [TM-01, DR-01, H-U0]
- q2lod.19: POST /api/portal/account/delete ({email,password} via the shared checkAccountPassword with IP + account throttle, or {token} via consumeResetToken's atomic single-use claim) and POST /account/delete-request (always the same 200). Both go through the one deleteAccountAskingClinics, which calls recordClientDeletionRequest per liveLinks row (links that still hold for the client, switch ignored) before deleting the account (cascade). Copy in DeleteSignedOutScreens.tsx and DeleteAccountByLink.tsx says up front that every clinic is asked. Reachable from SignInScreen ("Delete my account") and NoClinicsScreen; deletion email Universal Link /portal/reset?intent=delete opens DeleteByLinkScreen, and the web fallback in pages/portal/reset.tsx has no hook after its early return. middleware passes /api/* through to handlers. pg delete-reauth suite green. [UI-02, UI-07, H-P3, COM-02]
- q2lod.18: dispatchBookingCreated / dispatchPaymentCompleted return before any ledger or destination when appOrigin; only two consumer files (pages/api/public/book.ts two sites + payment-row flag; pages/api/webhooks/stripe.ts two sites reading updatedPayment.metadata). checkout.ts stamps appOrigin via a jsonb merge before any session URL is returned, and newSessionRowMetadata keeps it. Pixel loaders (MarketingPixels, GA, Aestheticc Meta pixel) go through useTagMayLoad/useTagsBlocked; usePixelVisibleUrl is mount-gated, so no pixel script reaches SSR HTML in the WebView before hydration. fireConversion only reaches pixels MarketingPixels actually loaded. The WebView sets applicationNameForUserAgent on every request plus the header on first load and native calls. app-origin-checkout-pay pg suite, app-origin unit, dispatch unit, booking money-flow and app-origin-no-tags DOM suites green. [TM-07, COM-02, H-P0, H-P1]
- q2lod.11: GET/HEAD /api/portal/verify never looks up or claims (only a read-only linkIsAppBound SELECT); POST redeems through redeem-magic-link.ts with the unchanged atomic UPDATE ... WHERE used_at IS NULL RETURNING, closed destination enum, 303. Confirm page CSP form-action 'self' and the relative same-origin 303 agree. Every producer of verify URLs (magic-link-token.ts) points to the same route; no remaining GET-redeeming consumer in lib/pages/components/mobile/mobile-portal. Mobile users without the app saw the same "Open in app" primary on main (verification-handoff.ts at 8c21d297c6), so that is not a regression. [DR-05, UI-07]
- q2lod.13: deviceNonce (32-128 [A-Za-z0-9-]) stored only as sha256 on portal_magic_links.device_nonce_hash (column already in 0370; no new migration); compare is timingSafeEqual; every refusal returns before the claim, so a refused link stays usable. App reads/mints the per-install keychain secret (WHEN_UNLOCKED_THIS_DEVICE_ONLY) and sends it on resolve-identity and every redemption; unbound links in the app go to ConfirmLinkScreen and re-run verify(url, true). expo-modules-core is declared at 3.0.30, the exact version expo 54.0.34 pins. [TM-01, H-U0]
Gates run by this seat at c93acdce68: pg (RUN_PG_TESTS, TZ=UTC, single files) login-reset-fence, delete-reauth, verify-redemption, app-origin-checkout-pay: 4 suites 24/24. Root targeted jest (10 suites incl. auth-flow, resolve-identity, verification-handoff, device-nonce, magic-link-token-binding, app-origin, app-origin-dispatch, booking money-flow, app-origin-no-tags, portal-reset-page): 296/296. mobile-portal jest 227/227, UI jest 78/78, tsc --noEmit 0.

Earlier findings:
- r1 P1 lib/portal/client-accounts/account-sessions.ts:94 closed: issuance now runs under lockAccount with a hash re-read (lines 69-99); fence pg suite green; removing the re-check fails the overtaken-login case.
- r1 P1 mobile-portal/app.json:138 closed: both server dispatchers return on appOrigin, book.ts and the webhooks carry the flag, checkout.ts stamps it before any URL, every pixel/tag loader is gated by the one helper; suites above green.
- r1 P2 pages/api/portal/account/index.ts:79 closed: POST /api/portal/account/delete re-authenticates by password or emailed link with no clinic session and deletes through deleteAccountAskingClinics; delete-reauth pg suite green.
- r1 P2 mobile-portal/src/app/PortalGate.tsx:248 closed: Delete my account is on SignInScreen and NoClinicsScreen, routing to DeleteAccountScreen / DeleteByLinkScreen (delete-signed-out-screens UI tests in the 78 green).
- r1 P1 pages/api/payments/[paymentId]/checkout.ts:645 closed: recordAppOriginOnPayment runs before the reuse/new-session branches (line 553) and newSessionRowMetadata keeps the flag; app-origin-checkout-pay pg suite green.
- r1 P3 pages/api/portal/invoices/[paymentId]/settle.ts:364 closed: settle issues no session; every app-paid row passes checkout.ts, which stamps it (same pg chain test).
- r1 P2 lib/portal/verify-confirm-page.ts:54-63 + components/portal/portal-error-reasons.ts:34-38 closed: an app-bound link renders no browser sign-in form, only Open in app (phone) and Request a new link.
- r1 P3 mobile-portal/src/services/link-device-nonce.ts:2 closed: expo-modules-core 3.0.30 declared in mobile-portal/package.json, matching expo's own pin.
- r1 P3 lib/portal/verify-confirm-page.ts:69-81 closed: the dead "Try the app link" is gone from the page.
- r1 P3 components/portal/portal-error-reasons.ts:38 closed: copy names "Access My Portal", the real button in lib/portal/magic-link-email.ts:47.
- r1 P3 lib/portal/client-accounts/reset-email.ts:51 open: accepted default (see P3 list).
- r1 P3 tests/pg-integration/portal-client-accounts-delete-reauth.test.ts:207 open: equivalent mutant, accepted (see P3 list).
- r1 P3 lib/portal/client-accounts/tokens.ts:172 + :206-241 open: unchanged at this tip, P3 grading kept (see P3 list).
- r1 P3 mobile-portal/src/services/account-session.ts:121-126 open: design limit of the 30 s grace (see P3 list).
- r1 P3 tests/integration/portal/data-export.test.ts:127 open: file unchanged since 8ee20401f7; test still stringifies logger calls (see P3 list).
- r1 P3 mobile-portal/src/services/account-session.ts:221 open: ACCEPTED P3 (Shane 24 Oct, q2lod.7.1).
- r1 P3 mobile-portal/src/app/PortalGate.tsx:168 open: ACCEPTED P3 (Shane 24 Oct, q2lod.7.1).
- r1 P2 pages/portal/[slug]/account.tsx:114 closed: portal-fetch-slug adds x-portal-slug and the shared guard 404s a mismatch; files unchanged since the cp1 re-review that executed it.
- r1 P3 tests/helpers/client-accounts-pg/idor-sweep.ts:549 closed: exact-count assertions in place (astra-tests cp1-fresh mutants red); file unchanged since.
- r1 P3 mobile-portal/PRIVACY_DECLARATIONS.md:22 closed: rationale matches landed fnrjn; doc/manifest consistency test in the green mobile-portal run.
- r1 P3 lib/email/booking-confirmation-actions.ts:198 closed: 96eac565cc threads the recipient address; not touched since.
- r1 P3 lib/email/booking-confirmation-actions.ts:170 closed: same fix as :198.
- r1 P3 pages/api/portal/preview.ts:154 closed: real preview-handler test present (tests/unit/portal/preview-auth-method.test.ts).
- r1 P2 components/portal/ClientAppSetting.tsx:61 closed: 8d118a6f70 edit-toggle-save DOM regression; untouched since.
- r1 P1 lib/portal/client-accounts/account-sessions.ts:195 closed: refresh/select/reset/sign-out all hold lockAccount (tokens.ts:67, account-sessions.ts:185/:241, sign-out.ts:31/:45).
- r1 P1 lib/portal/client-accounts/tokens.ts:132 closed: reuse revocation path revokes account tokens under the same fence (r3 PASS); unchanged except the tx parameter added to issueAccountScopedToken.
- r1 P2 lib/portal/client-accounts/account-sessions.ts:251 closed: usableLinks/liveLinkRows take tx everywhere in refresh/select/login (now including login at :82).
- r1 P3 lib/portal/client-accounts/enrolment.ts:60 closed: enrolFromSession refuses unless isClientAppEnabled (enrolment.ts:69).
- r1 P3 lib/portal/client-accounts/account-sessions.ts:190-194 closed: refreshSession runs in fenced(); a missing usable link throws Refuse and rolls back, so the token is not burned (comment and code at :241-262).
- r1 P3 lib/portal/client-accounts/account-sessions.ts:155 closed: selectClinic now runs in one transaction under lockAccount (:185).
- r1 P1 mobile-portal/src/app/PortalGate.tsx:118 closed: verify() supersedes the account session epoch before any network work (PortalGate.tsx:141).
- r1 P1 mobile-portal/src/app/PortalGate.tsx:92 + :112-155 closed: same supersede plus installLinkSession under the cookie lock; launch-order tests in the green run.
- r1 P2 mobile-portal/src/services/account-session.ts:84 closed: lost-reply retry with the same token and server 30 s grace; tests green.
- r1 P2 mobile-portal/src/services/account-session.ts:179 closed: local stores cleared first, server revocation bounded (clearPortalSession, settleWithin).
- r1 P2 mobile-portal/src/app/account-flows.ts:20-37 closed: offer gated on clinicHasClientApp (PortalGate.tsx verify, eligible(...)).
- r1 P3 mobile-portal/src/app/use-account-actions.ts:188-194 closed: sheet closes on delete (astra-app evidence; file untouched since).
- r1 P3 mobile-portal/src/app/PortalGate.tsx:319-321 closed: password-set note rendered before continuing; tests green.
- r1 P3 mobile-portal/src/app/use-account-actions.ts:188 closed: forgetEmail on delete; the new signed-out delete path also calls accountVault.forgetEmail (PortalGate deletedSignedOut).
- r1 P3 tests/integration/portal/client-accounts/credential-lifecycle.test.ts:225-237, :258-276 closed: rationale recorded (dcd30b3126); cross-device refusal and one-live-successor still asserted.
- r1 P2 pages/portal/[slug]/dashboard.tsx:232 closed: portal-fetch-slug wrapper + 24-route header sweep; untouched since cp1 re-review.
- r1 P2 mobile-portal/src/services/account-api.ts:95 closed: body parsing inside withRequestDeadline; the new deleteSignedOut/deleteRequest calls reuse the same request path; 227/227 green.
- r1 P2 mobile-portal/src/app/use-home-data.ts:105 closed: foreground refresh + UK-clock expiry tests in the green run.
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:158 closed: programme failure renders a retryable row (tests green).
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:136 closed: config-error recovery with Account and sign-in (tests green).
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:54 closed: row labels include subtitles (tests green).
- r1 P3 lib/portal/mobile-app-contract.ts:18 closed: narrow .dockerignore re-include of mobile-portal/src/api/types.ts.
- r1 P1 lib/portal/mobile-app-contract.ts:18-19 closed: same; Docker import-boundary meta test green at 8ee20401f7 (file untouched since).
- r1 P3 mobile-portal/src/app/PortalShell.tsx:30 closed: endpoint/schema-path Sentry tags allowlisted (sentry-portal-tags tests green).
- r1 P3 mobile-portal/src/app/ShellWebViewScreen.tsx:56 closed: Done from a Home-launched page returns Home; this tip only added the header/UA props.
- r1 P3 mobile-portal/src/app/HomeScreen.tsx:54 closed: same as the P2.
- r1 P3 mobile-portal/src/app/HomeScreen.tsx:158 closed: same as the P2.
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:128 closed: next-visit label includes the payment note (tests green).
- r1 P3 mobile-portal/src/app/use-home-data.ts:87 closed: quiet refresh keeps ok sections (tests green).
- r1 P3 mobile-portal/src/app/__tests__/home-screen-r1.test.tsx:121 closed: pending-refresh assertion present and green.
- r1 P3 mobile-portal/src/app/PortalShell.tsx:106 closed: onExit pinned in tests (green).
- r1 P3 mobile-portal/src/utils/__tests__/sentry-portal-tags.test.ts:28 closed: janedoe negative case present (green).
- r1 P2 mobile-portal/src/services/clinic-app-icon.ts:111 closed: one readPublicConfig; appIcon in configSchema and the contract test.
- r1 P3 mobile-portal/src/services/clinic-app-icon.ts:84 closed: cancellation and iOS-guard tests in the green run.
- r1 P3 lib/portal/client-accounts/app-icon.ts:8 closed: .js references; promise row UNVERIFIED until TestFlight.
- r1 P3 mobile-portal/scripts/build-clinic-icons.js:62 closed: relative and parameterised data-URL logos accepted.
- r1 P3 tests/config-effect/app-icon-toggle.config-effect.test.ts:288 closed: merged to sd1qc behaviour (staff 403), per the 8ee20401f7 resolution review.

UNVERIFIED
- Device/simulator: Universal Link vs custom-scheme handoff from Mail and Gmail, the in-app ConfirmLinkScreen, keychain nonce persistence across reinstall, Face ID, the WebView UA suffix and first-load header actually reaching the server, deletion-email link opening the app.
- Real email delivery (deletion and reset emails) and real Stripe webhook delivery for an app-paid row.
- Root npm run typecheck, lint, full npm test, full pg lane, Cloud Build simulation, npm run build (not run by this seat; builder/manager report them green).
- Other builder-reported mutants (q2lod.11/.13/.18/.19) not re-run here; only the login-fence mutant was.
- Landing-known items, not findings: drizzle/meta/_journal.json collision with main's 0370_ava_terms_acceptance (lander renumbers 0370/0371); file-length ratchet over-limit files (e.g. PortalGate.tsx grew) left to the lander.

```review-verdict
{"verdict":"PASS","findings":[{"severity":"P3","text":"lib/portal/client-accounts/tokens.ts:172 + :206-241 — carried: delayed original refresh can revoke the retry's delivered successor after a cold-instance timeout; client re-enters her password once. Unchanged, P3 grading kept."},{"severity":"P3","text":"mobile-portal/src/services/account-session.ts:121-126 — carried: app killed mid-refresh and reopened after the 30 s grace asks for the password instead of Face ID. Design limit."},{"severity":"P3","text":"tests/integration/portal/data-export.test.ts:127 — carried: privacy-log assertion stringifies logger calls, missing nested Error.message; handler safe. Assert exact metadata."},{"severity":"P3","text":"lib/portal/client-accounts/reset-email.ts:51 — carried, manager-accepted: deletion email held when every linked clinic is comms-paused; password delete still works."},{"severity":"P3","text":"tests/pg-integration/portal-client-accounts-delete-reauth.test.ts:207 — carried, accepted: 'deletes once' is an equivalent mutant (cascade); no user impact."},{"severity":"P3","text":"ACCEPTED q2lod.7.1 (Shane 24 Oct): mobile-portal/src/services/account-session.ts:221 delayed clinic selection after a link; own records under the wrong clinic label."},{"severity":"P3","text":"ACCEPTED q2lod.7.1 (Shane 24 Oct): mobile-portal/src/app/PortalGate.tsx:168 late config callback after a link; own records under the wrong clinic label."}]}
```
