REVIEWING: long-goal/aestheticcnext-q2lod @ c93acdce685da213ef31b84888a430a05f893d8b
FAIL
AestheticcNext-q2lod: NEEDS_CHANGES

P2 pages/api/portal/resolve-identity.ts:188 — A first-time iPhone client requests her enrolment link after reception saved her email with capital letters. The handler lowercases her input but compares it with the stored text case-sensitively: it returns 200 and sends nothing, preventing password setup. The real handler against PostgreSQL reproduced zero sends; lowercase-record and normalized-SQL controls each sent once. This lookup is inherited from main, but Phase 1 enrolment depends on it — normalize both sides of the lookup, cover the sibling request-access lookup, and test mixed-case clinic records through link issuance. [DR-05, UI-07, H-U0, H-D2]

- r1 P3 lib/portal/client-accounts/tokens.ts:172 + :206-241 open: an automatic retry that reaches the account lock before the delayed original can still have its delivered successor revoked by that original; request identity/idempotent rotation remains absent. Earlier P3 grading retained. [DR-01, DR-05]
- r1 P3 lib/portal/client-accounts/reset-email.ts:51 open: ACCEPTED manager default; deletion-link email remains held when every linked clinic pauses communications. Password-authenticated deletion remains available. [COM-01]
- r1 P3 mobile-portal/src/services/account-session.ts:221 open: ACCEPTED q2lod.7.1; delayed selection can install the client’s own clinic-A session after clinic B’s link. Deferred by Shane.
- r1 P3 mobile-portal/src/app/PortalGate.tsx:168 open: ACCEPTED q2lod.7.1; a delayed clinic-A configuration callback can restore A’s screen after B’s link. Deferred by Shane.

VERIFIED OK

All checks below concern the pinned tip. **35 targeted suites passed: 263 server/contract tests, 18 component tests and 127 PostgreSQL tests.** Files remain unchanged.

- r1 P2 pages/portal/[slug]/account.tsx:114 closed: export carries the displayed slug; account-page, fetch-wrapper and shared mismatch tests passed. [TM-01, UI-07]
- r1 P3 tests/helpers/client-accounts-pg/idor-sweep.ts:549 closed: exact collection assertions remain present and the PostgreSQL tenancy suite passed; its historical filter mutations were not independently repeated. [DR-06]
- r1 P3 lib/email/booking-confirmation-actions.ts:198 closed: traced customerEmail through recipientEmail into emailAtIssue; differing-recipient regression passed. [COM-02, COM-03]
- r1 P3 lib/email/booking-confirmation-actions.ts:170 closed: the confirmation’s actual recipient now supplies the manage-link proof stamp; regression passed. [COM-02]
- r1 P3 pages/api/portal/preview.ts:154 closed: the real preview-handler test asserted authMethod=preview and no proved email. [TM-01, H-D2]
- r1 P2 components/portal/ClientAppSetting.tsx:61 closed: rendered edit→toggle→save regression preserved and saved the unsaved welcome message. [UI-04, UI-07]
- r1 P1 lib/portal/client-accounts/account-sessions.ts:195 closed: PostgreSQL credential-fence tests passed for refresh versus reset and concurrent replay. [TM-01, DR-01]
- r1 P1 lib/portal/client-accounts/tokens.ts:132 closed: genuine reuse invalidated the outstanding accountToken; subsequent clinic selection was refused in the PostgreSQL test. [TM-01, DR-05]
- r1 P2 lib/portal/client-accounts/account-sessions.ts:251 closed: traced transaction propagation through usableLinks and switch readers; PostgreSQL refresh/select regression passed. [DR-01, DR-08]
- r1 P3 lib/portal/client-accounts/enrolment.ts:60 closed: the off-switch PostgreSQL case refused enrolment, created no account, then succeeded after enabling the switch. [UI-06]
- r1 P3 lib/portal/client-accounts/account-sessions.ts:190-194 closed: refresh now falls back when the bound clinic switches off; refusal rolls rotation back. PostgreSQL fallback and unconsumed-token controls passed. [DR-01, DR-05]
- r1 P3 lib/portal/client-accounts/account-sessions.ts:155 closed: selection now runs inside the account-lock transaction; traced the native picker’s single-flight guard against the reported double tap. [DR-01]
- r1 P2 pages/portal/[slug]/dashboard.tsx:232 closed: traced Book now through the installed slug wrapper; wrapper and route-sweep tests passed, including wrong-clinic refusal before handler execution. [TM-01, UI-05, H-B1]
- r1 P3 tests/integration/portal/client-accounts/credential-lifecycle.test.ts:225-237, :258-276 closed: the changed retry contract is documented in the tests and token implementation; PostgreSQL lost-reply, genuine-reuse and cross-device controls passed. [DR-06]
- r1 P3 lib/portal/mobile-app-contract.ts:18 closed: Docker import-boundary test passed with the narrow pure-type inclusion. [DR-06]
- r1 P1 lib/portal/mobile-app-contract.ts:18-19 closed: both imports now resolve through the included, dependency-free types file; Docker boundary and runtime contract tests passed. [DR-06]
- r1 P3 lib/portal/client-accounts/app-icon.ts:8 closed: build-script references use .js; the promise inventory explicitly retains UNVERIFIED device status. [H-D5]
- r1 P3 tests/config-effect/app-icon-toggle.config-effect.test.ts:288 closed: sd1qc’s shared requireClinicAdmin guard is retained; both settings families passed their updated staff-refusal cases. [H-T1, H-D0]
- r1 P1 mobile-portal/app.json:138 closed: traced app-origin booking/payment decisions into the dispatcher and stored payment metadata; no-tag, destination-dispatch and PostgreSQL app-paid checkout/webhook tests passed with web controls. [TM-07, COM-02, H-P0, H-P1]
- r1 P2 pages/api/portal/account/index.ts:79 closed: clinic-independent password/email reauthentication now reaches shared deletion; PostgreSQL disabled-clinic, zero-clinic, wrong-password and emailed-link cases passed. [UI-02, H-P3]
- r1 P1 pages/api/payments/[paymentId]/checkout.ts:645 closed: checkout persists appOrigin before returning reused or new sessions; PostgreSQL web-created→app-paid→webhook cases dispatched nothing. [TM-07, COM-02]
- r1 P3 pages/api/portal/invoices/[paymentId]/settle.ts:364 closed: settlement’s payment proceeds through the corrected checkout path; the retired-session PostgreSQL case preserved tracking suppression. [COM-02, DR-05]
- r1 P2 lib/portal/verify-confirm-page.ts:54-63 + components/portal/portal-error-reasons.ts:34-38 closed: live app-bound links get app-only copy without browser redemption; confirmation-page and PostgreSQL binding tests passed. [UI-01, H-U0]
- r1 P3 lib/portal/verify-confirm-page.ts:69-81 closed: the dead “Try the app link” option is removed; the phone page retains the custom-scheme Open in app and new-link recovery. [UI-01]
- r1 P3 components/portal/portal-error-reasons.ts:38 closed: recovery copy now names Access My Portal followed by Open in app. [H-U0]
- r1 P1 lib/portal/client-accounts/account-sessions.ts:94 closed: all three PostgreSQL login/reset ordering cases passed. An independent in-memory product-module probe recreated late credential issuance when the hash recheck was removed; normal controls passed both ways. [TM-01, DR-01]
- r1 P2 lib/.../data-export.ts:66 closed: the current export catch passes class/IDs only to logging and no raw error to the response; inspected source and targeted handler tests passed. [H-P1]
- r1 P2 account-delete.ts:92 closed: the current deletion catch passes class/IDs only; inspected source and targeted handler tests passed. [H-P1]
- r1 P3 gdpr-request-alert.ts:26 closed: client copy says 30 days, matching the computed response deadline. [H-U0]

Migration 0370 is byte-identical to checkpoint 1. Both added snapshots chain correctly, and PostgreSQL erasure tests cover the new account stores. Merge-tree against the available origin/main reported only the expected journal conflict. Migration renumbering and ratchet regeneration remain lander work.

UNVERIFIED

- Real-device/TestFlight journeys, Face ID, cookie-store transport, Universal Links, native icon compilation/switching and real email delivery.
- Full build, full PostgreSQL lane and Cloud Build simulation; deliberately not run.
- Rollback execution and production activation.
- Broader historical mutation claims. Unsuccessful in-memory Jest interception attempts did not alter executed modules and are excluded from evidence.
- Dedicated findings in the separate mobile-app and test-quality slices, beyond the cross-slice checks listed above.

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P2","text":"pages/api/portal/resolve-identity.ts:188 — A first-time iPhone client whose clinic holds a mixed-case email receives a successful link-request response but no email: input is lowercased and compared case-sensitively with clients.email. Real-handler PostgreSQL probe reproduced zero sends; lowercase-record and normalized-SQL controls each sent once. Inherited from main, but Phase 1 enrolment depends on this lookup. Normalize both sides, cover request-access, and test mixed-case records through link issuance. [DR-05, UI-07, H-U0, H-D2]"},{"severity":"P3","text":"r1 lib/portal/client-accounts/tokens.ts:172 + :206-241 open — An automatic refresh retry can rotate before the delayed original, which then revokes the delivered successor's family. Retry identity/idempotent rotation remains absent; earlier P3 grading retained. [DR-01, DR-05]"},{"severity":"P3","text":"r1 lib/portal/client-accounts/reset-email.ts:51 open — ACCEPTED manager default: deletion-link email remains held when every linked clinic pauses communications; password-authenticated deletion remains available. [COM-01]"},{"severity":"P3","text":"r1 mobile-portal/src/services/account-session.ts:221 open — ACCEPTED q2lod.7.1: delayed clinic selection can install the client's own clinic-A session after clinic B's link; deferred by Shane."},{"severity":"P3","text":"r1 mobile-portal/src/app/PortalGate.tsx:168 open — ACCEPTED q2lod.7.1: delayed clinic-A configuration can restore A's screen after clinic B's link; deferred by Shane."}]}
```
