# REVIEW 3bm73 — SONNET 5.5 FRESH (independent, patient data)

REVIEWING: goal/aestheticcnext-3bm73 @ 302cd5a79eb4911dc07255d890ff90b2761a4762

Date 2026-10-23. Base origin/main. Diff: use-consent-client.ts (new), workflow-stepper.tsx, consent-step.tsx, workflow-consent-medical-history.test.tsx. Covers closed duplicate 76yto.

FAIL

Findings:
- P2 pages/api/clients/[id]/medical-history.ts:164 + lib/forms/prefill.ts:251 + lib/forms/consent-variables.ts:310 — H-U0 missing becomes "No" (newly exposed on this screen; same defect already tracked as AestheticcNext-e5hw9): the GET fills `smoking: false` for every exists:true record, and CSV import (pages/api/clients/import/batch.ts:458) creates a record with no smoking value. A practitioner opens the protocol Consent step for an imported client, and the consent text now prints "Smoking: No" and the smoking radio is preselected "no". The client signs a clinical statement nobody recorded. This branch is what puts that value on this screen — fix: stop defaulting smoking in the GET (or have the helpers treat it as unknown unless the stored record carries it), add a test on an exists:true record with no smoking key that expects blank and unselected.
- P3 tests/components/appointments/workflow-consent-medical-history.test.tsx — H-D1 the failed-fetch guard at components/appointments/use-consent-client.ts:39 (throw instead of caching an empty record, because the contraindication checker shares the key) has no test; reverting it to `return { medicalHistory: {}, exists: false }` leaves all 4 tests green — fix: add a test where the medical-history GET returns 500 and assert tokens are blank, nothing is prefilled, and the cached value is not an empty record. Not user-facing in this diff alone, so it may land on a one-line builder rejection.

VERIFIED OK:
- Which client: useConsentClient(clientId from the workflow, ...) reads GET /api/clients/[clientId]/medical-history, the same endpoint the Medical History step, Consent & Documents tab, contraindication checker and prescription form use. The handler resolves getActiveBusinessId, loads the client with `clients.id AND clients.businessId`, and returns 404 otherwise. The allergies reads are `clientId AND businessId`. The `clientMedicalHistory` select is by clientId only, but it sits behind that business-scoped client check (existing code, not changed). H-T3 / TM-01 OK.
- Missing never becomes No for pregnancy: an empty `contraindications: []` (the API default) returns "" from pregnancyConsentValue and no prefill; only a recorded Pregnancy/Breastfeeding contraindication gives "Yes", and an explicit legacy `false` gives "No". The prefill answers "yes" only, and only when "yes" is an option (lib/forms/pregnancy-status.ts). Conditions, medications and allergies are blank when absent. No record at all (exists:false) returns only NOK fields and orphan allergies, so tokens are blank and nothing is prefilled. Test 2 covers this.
- Failed or slow fetch: the query throws on !ok, savedHistory becomes null and medicalHistory falls back to allergies only (or null), so tokens are blank, never "No". Late arrival re-runs the prefill effect, and the effect never overwrites a value the user already typed (consent-step.tsx hasExisting guard). Test 4 covers late arrival.
- Cache-shape sharing: the hook returns `json.data ?? json`, which is the { medicalHistory, exists } shape the other four readers cache under ["client", id, "medical-history"] (apiClient unwraps the envelope). No shape clash.
- Stale data: handleStepComplete invalidates the key before the next step, so a Medical History step saved earlier in the protocol feeds the later consent step. Mutation M3 (remove invalidate) turns test 3 red.
- H-D0 second path: no new endpoint and no second mapping. The hook only fetches; mapping stays in buildConsentVariableValues (consent-variables.ts:265) and buildFormPrefill (prefill.ts:33). It reuses the existing key. Extracting the builder to a hook is justified by the file-length ceiling. H-D0 clear.
- help content: the 'not yet on a consent step inside a treatment protocol' qualifier is not present in lib/help/content.ts at the tip or on main (forms-prefill-medical-history, line 735). Nothing to drop.
- H-P1: no new logging. H-C*/H-M*: not applicable. H-D4: new test is in the active components project, not in any ignore list.
- Mutation testing (targeted file, 4 tests; baseline 4 green):
  - M1 hook drops saved history -> 3 red
  - M2 buildFormPrefill drops medicalHistory -> 3 red
  - M3 no invalidate -> 1 red (test 3)
  - M4 remove clientMedicalHistory from the effect deps -> 1 red (test 4)
  - M8 pregnancy token -> red
  - M9 smoking token -> red
  - M10 conditions token -> red
  - Survivors: M6 (failed fetch returns an empty record) and M7 (allergies ignore the saved record). M7 is equivalent, since both sources read the same table.
- Targeted suites workflow-stepper-edit-redo, consent-step-client-mode and consent-step-template-gate: 30 passed.
- Step 4 landing check: git merge-tree vs origin/main shows 0 conflicts. Neither stepper file changed on main since the merge-base. No VERSION, CHANGELOG, baseline or migration touches in the diff.

UNVERIFIED:
- `npx tsc --noEmit` crashed in my scratch worktree (node heap exit), so the type check is not confirmed by me.
- Not driven in a live app. The test drives the real WorkflowStepper and ConsentStep with a mocked fetch, not the real handler or pg. The smoking default in finding 1 was read from the handler code, not run against prod.
- Allure 28d4ccce on a protocol step: taken from the bead's prod premise, not re-queried.

```review-verdict
{"verdict": "FAIL", "findings": [{"severity": "P2", "text": "pages/api/clients/[id]/medical-history.ts:164 + lib/forms/prefill.ts:251 + lib/forms/consent-variables.ts:310 — H-U0 missing becomes \"No\" (newly exposed on this screen; same defect already tracked as AestheticcNext-e5hw9): the GET fills `smoking: false` for every exists:true record, and CSV import (pages/api/clients/import/batch.ts:458) creates a record with no smoking value. A practitioner opens the protocol Consent step for an imported client, and the consent text now prints \"Smoking: No\" and the smoking radio is preselected \"no\". The client signs a clinical statement nobody recorded. This branch is what puts that value on this screen — fix: stop defaulting smoking in the GET (or have the helpers treat it as unknown unless the stored record carries it), add a test on an exists:true record with no smoking key that expects blank and unselected."}, {"severity": "P3", "text": "tests/components/appointments/workflow-consent-medical-history.test.tsx — H-D1 the failed-fetch guard at components/appointments/use-consent-client.ts:39 (throw instead of caching an empty record, because the contraindication checker shares the key) has no test; reverting it to `return { medicalHistory: {}, exists: false }` leaves all 4 tests green — fix: add a test where the medical-history GET returns 500 and assert tokens are blank, nothing is prefilled, and the cached value is not an empty record. Not user-facing in this diff alone, so it may land on a one-line builder rejection."}]}
```
