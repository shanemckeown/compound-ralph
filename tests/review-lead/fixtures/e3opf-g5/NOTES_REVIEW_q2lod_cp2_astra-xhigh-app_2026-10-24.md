REVIEWING: long-goal/aestheticcnext-q2lod @ c93acdce685da213ef31b84888a430a05f893d8b
PASS
AestheticcNext-q2lod: PASS

No new P1/P2 found in the APP slice.

- r1 P3 mobile-portal/src/services/account-session.ts:221 open: ACCEPTED q2lod.7.1 — an iPhone client’s delayed clinic selection can install her own clinic A session after opening clinic B’s link — fix deferred by Shane.
- r1 P3 mobile-portal/src/app/PortalGate.tsx:168 open: ACCEPTED q2lod.7.1 — an iPhone client’s delayed clinic A config callback can restore A’s screen after opening clinic B’s link — fix deferred by Shane.
- r1 P3 mobile-portal/src/services/account-session.ts:121-126 open: an iPhone client closes the app during a committed refresh; returning after the server’s 30-second grace requires her password — recovery remains in-process only; document the limit or persist replay-safe recovery. Earlier P3 grading retained. [DR-05, UI-07]
- r1 P3 lib/portal/client-accounts/tokens.ts:172 + :206-241 open: an iPhone refresh retry can reach the server before its delayed original; the original then invalidates the successor held by the app — distinguish retry identity or make rotation idempotent. Traced through the app’s automatic retry; PostgreSQL reproduction was not rerun. Earlier P3 grading retained. [DR-01, DR-05]

VERIFIED OK

All checks below concern the pinned tip. Targeted execution: **39 suites, 427 tests passed**. Six in-memory mutations failed their relevant assertions, with controls passing. Jest cache persistence was disabled in memory; mobile tests used the shared SWC transformer and existing mobile dependencies. Icon output used an in-memory filesystem with real Sharp rendering. No files changed.

- r1 P2 mobile-portal/src/services/account-api.ts:95 closed: deadline and lost-body tests pass; moving body parsing outside the deadline fails the stalled-body test while the genuine-refusal control passes. [UI-07, DR-06, H-D1]
- r1 P1 mobile-portal/src/app/PortalGate.tsx:118 closed: cold/warm link-versus-refresh tests pass; removing session supersession fails two race assertions with twelve controls passing. [TM-01, DR-06]
- r1 P1 mobile-portal/src/app/PortalGate.tsx:92 + :112-155 closed: traced link installation through the shared cookie lock and both cookie stores; launch-order and cookie-install tests pass. [TM-01, H-B1]
- r1 P2 mobile-portal/src/services/account-session.ts:84 closed: lost-response tests exercise same-token retry, successful recovery and retention after two lost replies. [DR-05, UI-07]
- r1 P2 mobile-portal/src/services/account-session.ts:179 closed: stalled-network sign-out tests confirm local clearing before network completion and the eight-second server-wait cap. [UI-07, H-U0]
- r1 P2 mobile-portal/src/app/account-flows.ts:20-37 closed: eligibility and rendered-gate tests suppress Set a password when the clinic’s Client app switch is off. [UI-06, H-U2]
- r1 P3 mobile-portal/src/app/use-account-actions.ts:188-194 closed: the rendered delete-then-sign-in test confirms the Account sheet remains closed.
- r1 P3 mobile-portal/src/app/PortalGate.tsx:319-321 closed: the rendered password-set/login-failure test displays the confirmation before continuing.
- r1 P3 mobile-portal/src/app/use-account-actions.ts:188 closed: deletion calls forgetEmail and clears the gate’s known email; the rendered deletion regression passes.
- r1 P2 mobile-portal/src/app/use-home-data.ts:105 closed: foreground-refresh and UK-clock tests pass, including exclusion of already-started visits. [DR-02, DR-05]
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:158 closed: a failed programme request renders a retryable Programme row; the rendered regression passes. [UI-02, UI-07]
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:136 closed: config failure preserves Account and Return to sign-in; the rendered recovery test passes. [UI-02, H-U2]
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:54 closed: rendered accessibility assertions include package sessions, voucher balance and visit counts. [UI-03, UI-05]
- r1 P3 lib/portal/mobile-app-contract.ts:18 closed: the Docker import-boundary test passes with only the pure API types re-included.
- r1 P1 lib/portal/mobile-app-contract.ts:18-19 closed: the existing Docker import-boundary check executes successfully against this tree. [DR-06]
- r1 P3 mobile-portal/src/app/PortalShell.tsx:30 closed: decode failures retain endpoint and schema-path tags through the tested Sentry allowlist. [TM-07, H-P0]
- r1 P3 mobile-portal/src/app/ShellWebViewScreen.tsx:56 closed: rendered tests confirm Home-launched Back/Done exits to Home and portal-tab Done retains its tab.
- r1 P3 mobile-portal/src/app/HomeScreen.tsx:54 closed: the row-label regression confirms visible subtitles are spoken.
- r1 P3 mobile-portal/src/app/HomeScreen.tsx:158 closed: the programme-error regression confirms the row remains visible and retries.
- r1 P2 mobile-portal/src/app/HomeScreen.tsx:128 closed: the next-visit label includes Waiting for payment; removing that label fragment fails its assertion with twelve controls passing. [UI-03, H-U0]
- r1 P3 mobile-portal/src/app/use-home-data.ts:87 closed: rendered tests confirm quiet failures preserve successful sections and genuine session refusal ends the session.
- r1 P3 mobile-portal/src/app/__tests__/home-screen-r1.test.tsx:121 closed: the pending foreground-refresh test executes and confirms existing Home rows remain rendered.
- r1 P3 mobile-portal/src/app/PortalShell.tsx:106 closed: Home-return and portal-tab Done tests execute and pass.
- r1 P3 mobile-portal/src/utils/__tests__/sentry-portal-tags.test.ts:28 closed: negative assertions include safe-looking non-endpoints janedoe and abc123; both are dropped.
- r1 P2 mobile-portal/src/services/clinic-app-icon.ts:111 closed: icon and switch readers share readPublicConfig; removing appIcon from the decoder fails both contract-drift cases with nineteen controls passing. [H-D0, H-D1]
- r1 P3 mobile-portal/src/services/clinic-app-icon.ts:84 closed: removing cancellation fails the pending-read sign-out test; removing the iOS guard fails the Android test; eight controls pass in each mutation.
- r1 P3 lib/portal/client-accounts/app-icon.ts:8 closed: script references name the existing .js file, and the promise inventory explicitly remains UNVERIFIED pending TestFlight. [H-D5]
- r1 P3 mobile-portal/scripts/build-clinic-icons.js:62 closed: the real rendering tests produce valid icons from relative and parameterised data-URL logos.
- r1 P3 tests/config-effect/app-icon-toggle.config-effect.test.ts:288 closed: merged staff expectations and both configuration-effect suites pass under the shared admin guard. [H-T1]
- r1 P3 mobile-portal/PRIVACY_DECLARATIONS.md:22 closed: the rationale reflects landed fnrjn, zero patient-page tracing and remaining non-patient tracing.
- r1 P2 mobile-portal/PRIVACY_DECLARATIONS.md:22 closed: privacy/config tests confirm declared analytics types and agreement between the manifest and ASC answers.
- r1 P2 mobile-portal/app.json:122 closed: PerformanceData remains declared linked; the config assertion passes.
- r1 P2 mobile-portal/app.json:130 closed: ProductInteraction remains declared linked; the config assertion passes.
- r1 P2 mobile-portal/PRIVACY_DECLARATIONS.md:23 closed: the document names pageviews and interaction events after consent.
- r1 P2 mobile-portal/PRIVACY_DECLARATIONS.md:6 closed: the introduction consistently declares analytics data linked.
- r1 P2 mobile-portal/app.json:108 closed: DeviceID includes Analytics; manifest/document consistency assertions pass.
- r1 P2 components/portal/ClientAppSetting.tsx:61 closed: the rendered edit→toggle→save regression preserves the unsaved greeting. [UI-04, H-U0]
- r1 P2 pages/portal/[slug]/account.tsx:114 closed: the export request names its slug; account-page, fetch-wrapper and mismatch-sweep tests pass. [TM-01, H-B1]
- r1 P2 pages/portal/[slug]/dashboard.tsx:232 closed: the installed fetch wrapper supplies the displayed clinic slug; wrapper and route-sweep tests refuse mismatches. [TM-01, UI-05]
- r1 P1 mobile-portal/app.json:138 closed: traced native Home booking into the marked WebView and shared app-origin decision; dispatcher and rendered tag-loader tests suppress app advertising while web controls pass. [TM-07, H-P0, H-P1]
- r1 P2 pages/api/portal/account/index.ts:79 closed: the reachable signed-out deletion screens use clinic-independent password/email proof and the shared deletion core; rendered flows pass. [UI-02, H-P3]
- r1 P2 mobile-portal/src/app/PortalGate.tsx:248 closed: sign-in exposes Delete my account without opening a clinic; password and emailed-link deletion flows execute successfully in rendered tests. [UI-02, H-U2]
- r1 P1 pages/api/payments/[paymentId]/checkout.ts:645 closed: code tracing confirms recordAppOriginOnPayment runs before reused/new checkout URLs, new-session metadata preserves the flag, and both webhook dispatch sites read it. [COM-02, H-P1]
- r1 P3 pages/api/portal/invoices/[paymentId]/settle.ts:364 closed: traced settlement through checkout’s persisted app-origin decision before session handoff; the correction covers the downstream webhook consumer.
- r1 P2 lib/portal/verify-confirm-page.ts:54-63 + components/portal/portal-error-reasons.ts:34-38 closed: handler tests confirm app-bound links omit browser sign-in and provide the custom-scheme Open in app route. [UI-01, H-U0]
- r1 P3 mobile-portal/src/services/link-device-nonce.ts:2 closed: expo-modules-core is explicitly declared; nonce and custom-scheme redemption tests pass.
- r1 P3 lib/portal/verify-confirm-page.ts:69-81 closed: the dead Try the app link is removed; confirmation-page tests pass.
- r1 P3 components/portal/portal-error-reasons.ts:38 closed: recovery copy names Access My Portal followed by Open in app; its regression assertion passes.

Applicable hazard checks: H-T1/T2 controls default off, save/reload and isolate clinics; H-T3/H-B1 clinic scope reaches consumers; H-P0/P1 Sentry and tracking boundaries are tested; H-P2 Home uses client-facing fields; H-P3 deletion reuses the existing core; H-U0/U1/U2 preserve truthful states and recovery; H-D0 uses shared config, cookie and deletion paths; H-D1–D6 have targeted behavioral, contract, fixture, discovery, evidence and UK-time checks. H-C1 retains the established transactional-send pause policy; H-C4 activation still requires clinic opt-in and icon generation before release. H-M1–M7, H-C2, H-B3–B5 and H-U3 introduce no new behavior in this APP slice.

Merge-tree against local origin/main c9bb3145ddf5b51e4168159c13a5c31ef5d7d460 reports only the expected drizzle/meta/_journal.json conflict. Migration renumbering and ratchet regeneration remain lander work.

UNVERIFIED

- Physical-device/simulator journeys: Face ID, Keychain persistence, actual cookie transport, Universal Links from mail browsers, VoiceOver, Dynamic Type and icon switching.
- Native compilation/prebuild, frozen install, typechecking, production build, full test lanes and Cloud Build simulation.
- PostgreSQL race/deletion/payment-chain reproductions; handler tests here used mocked storage.
- Live email, push, Sentry delivery, production settings and bundled clinic-logo inventory.
- Other pushed branches and the eventual combined landing tree.

```review-verdict
{"verdict":"PASS","findings":[{"severity":"P3","text":"r1 mobile-portal/src/services/account-session.ts:221 open: ACCEPTED q2lod.7.1 — an iPhone client's delayed clinic selection can install her own clinic A session after opening clinic B's link — fix deferred by Shane."},{"severity":"P3","text":"r1 mobile-portal/src/app/PortalGate.tsx:168 open: ACCEPTED q2lod.7.1 — an iPhone client's delayed clinic A config callback can restore A's screen after opening clinic B's link — fix deferred by Shane."},{"severity":"P3","text":"r1 mobile-portal/src/services/account-session.ts:121-126 open: an iPhone client closes the app during a committed refresh; returning after the server's 30-second grace requires her password — recovery remains in-process only; document the limit or persist replay-safe recovery. Earlier P3 grading retained. [DR-05, UI-07]"},{"severity":"P3","text":"r1 lib/portal/client-accounts/tokens.ts:172 + :206-241 open: an iPhone refresh retry can reach the server before its delayed original; the original then invalidates the successor held by the app — distinguish retry identity or make rotation idempotent. Traced through the app's automatic retry; PostgreSQL reproduction was not rerun. Earlier P3 grading retained. [DR-01, DR-05]"}]}
```
