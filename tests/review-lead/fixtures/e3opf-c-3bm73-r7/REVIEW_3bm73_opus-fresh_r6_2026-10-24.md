REVIEWING: goal/aestheticcnext-3bm73 @ 4f1c34f25e75593f87ca09fc2b4639c63b174e23

PASS

Verdict: PASS

Reviewer: Opus fresh head-to-head seat (patient data), round 6, 2026-10-24. Read-only against the detached checkout /tmp/rev-3bm73-r6. No tracked file modified; scratch probes ran as a temporary test file, since deleted (copy kept at /tmp/rev-3bm73-r6-opus-scratch/zz-opus-scratch-r6.test.tsx).

No P1, P2 or P3 findings at this tip.

## VERIFIED OK

### Part A: prior findings r1 to r5

- r2 P1 components/appointments/use-consent-client.ts:73 closed: medicalHistory now comes only from `historyQuery.isSuccess` on this opening's own key (use-consent-client.ts:41-62). The stepper no longer threads the banner's clientAllergies into the consent client (workflow-stepper.tsx diff removes the `{ allergies: clientAllergies }` object). The failed-read tests in workflow-consent-medical-history.test.tsx (lines 454, 482, 589) assert opening status `error`, banner still shows "Lidocaine", and every token blank ("Conditions=; Meds=; Smoking=; Allergies=; Pregnant=;"). All pass.
- r2 P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36 closed: `touchedFieldsRef` records every field set through `setTemplateFieldValue` (consent-step.tsx:227-230), cleared ones included. `mergeGeneratedPrefill` skips any touched field (merge-generated-prefill.ts:38). Part B probe (i) through the real ProtocolConsentStep and useConsentClient signed `{"medication":"","smoker":"no"}`, with no Sertraline.
- r2 P3 pages/api/clients/import/batch.ts:418 closed: `smoking: false` was removed from the import insert (batch.ts:418). client-import.test.ts:1040-1047 now asserts that the one history insert has `conditions ["Asthma"]` and `not.toHaveProperty("smoking")`, so restoring `smoking:false` fails that assertion. The suite passes. I did not run an in-place mutation because tracked files are read-only here.
- r1 P1 components/appointments/workflow-steps/consent-step.tsx:256 closed: every opening reads under `[...prefix, "opening", openingId]` with `gcTime: 0`, so no cached copy is ever shown. Generated answers are tracked in `generatedPrefillRef`, so a later record replaces or clears an untouched generated answer (merge-generated-prefill.ts:40-49). The stale-pregnancy and stale-smoking tests in workflow-consent-medical-history.test.tsx pass.
- r1 P2 pages/api/clients/[id]/medical-history.ts:164 closed: the GET no longer defaults `smoking: false` (medical-history.ts:164), and import no longer writes it. prefill.ts:251 and consent-variables.ts:310 both treat `undefined` as unknown. medical-history.test.ts adds "leaves smoking unset when the record never stored it", which passes. One residual is pre-existing and out of this branch: the PUT writes `smoking: data.smoking || false` (medical-history.ts:362) because the Medical History form's smoking control is an on/off switch shown as "No" when off. That is the AestheticcNext-e5hw9 territory, not this branch.
- r1 P3 tests/components/appointments/workflow-consent-medical-history.test.tsx closed: three tests now drive a failing medical-history GET and assert blank tokens, nothing prefilled and an `error` state on the opening's key. Reverting the throw to return an empty record would turn that state to `success` and fail them.
- r4 P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36 closed: this is the same mechanism as r2 F2. Part B probe (i) is below, and consent-step-cleared-prefill.test.tsx passes.
- r5 P1 components/appointments/use-consent-client.ts:57 closed: the form's useConsentClient (inside ProtocolSignatureStep) uses a per-mount `openingId` key, so it cannot join the panel's in-flight request. The panel's late response lands under the panel's own key. Part B probe (ii) is below, and the new regression test in protocol-consent-resign-medical-history.test.tsx passes.

### Part B: re-run probes at this tip (in memory, real ProtocolConsentStep, real useConsentClient, real React Query)

- (i) r4 probe. The Medical History GET is held. The user types "Sertr" into Current medication, clears it, then the held response returns `{medications:["Sertraline"], smoking:false}`. Token shows "Meds=Sertraline; Smoking=No; Pregnant=;" and the field stays "". Signed PUT `data.checkboxes` = `{"medication":"","smoker":"no"}`. No Sertraline is in the payload; smoker "no" is the recorded `smoking:false`.
- (ii) r5 probe. The Already signed panel's GET #1 (stale: `contraindications:["Pregnancy"], smoking:false`) is held. The user taps Re-sign. The form's own GET #2 returns fresh data (`contraindications:[], smoking:true`). Then GET #1 is released late. Exactly 2 history GETs were made; token "Smoking=Yes; Pregnant=;". Signed PUT (action edit) `data.checkboxes` = `{"smoker":"yes"}`, with no pregnant "yes".

### Part C: other checks

- Every test file that imports a changed file passed: 28 suites, 577 tests, pg-integration excluded. Branch component suites: 3 suites, 17 tests. `tsc --noEmit` reported no errors in the changed files.
- `ProtocolSignatureStep` overrides the outer (panel) `client` after the spread (protocol-consent-step.tsx:24), so the panel's record never reaches the form.
- `ConsentStepWithExisting` keys the gate by workflow, step, mode and template, so a template change remounts the form. `touchedFieldsRef` and `generatedPrefillRef` therefore never carry over between templates.
- FormFieldRenderer does not call onChange on mount (DateField's effect only sets local state), so no field is marked touched before the prefill arrives.
- `clientMedicalHistory` keeps the same reference across identical refetches (structural sharing), so the prefill effect does not churn.

## UNVERIFIED

- I did not run the pg-integration lane (tests/pg-integration/medical-history-update-trail.test.ts) or `npm run build`.
- Parked P4 note (per CLINIC_REALITY, not blocking): probe (iii). After an opening's read succeeds, a later background refetch of that key that fails (both attempts, retry 1) flips `isSuccess` to false. All untouched generated medical answers and tokens then clear to blank. Measured: Medication "Sertraline" went to "", and tokens went blank. Answers the user typed are kept. This fails safe (unknown, never a wrong value). It needs an invalidation or a reconnect refetch while the form is open, plus a server error on that refetch. If wanted later, keep the last successful record while `isError && data`.
- Parked P4 note: probe (iv). If the prefix is invalidated while the opening's first GET is still in flight, React Query reuses that in-flight request instead of cancelling it (it cancels only when data already exists). The older response is then accepted (pregnant "Yes" prefilled). This contradicts the docblock at use-consent-client.ts:8-9. In practice, the only invalidators are the client page's Medical History tab (another page) and the stepper's `handleStepComplete`, which awaits the invalidation before the next consent step mounts. Neither can fire during an open consent form's first read in ordinary single-operator use.
- Minor: every protocol consent opening now makes two Medical History GETs (the outer panel reader and the form's reader), so two `read medical_history` audit rows are written. This is harmless and not a finding.

```review-verdict
{"verdict":"PASS","findings":[]}
```
