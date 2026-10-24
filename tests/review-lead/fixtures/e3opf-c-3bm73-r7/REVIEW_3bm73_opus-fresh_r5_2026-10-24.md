REVIEWING: goal/aestheticcnext-3bm73 @ 047e04a053ede15e107c8cb6d512653d8772ad94
PASS

(no P1/P2/P3 findings)

VERIFIED OK

PART A: prior findings, each checked independently at 047e04a053

- r2 P1 components/appointments/use-consent-client.ts:73 closed: medicalHistory comes only from `settledRef`, which is written only when `isSuccess && isFetchedAfterMount` for this observer. clientAllergies (the safety banner list) is no longer threaded into the consent client: the workflow-stepper `consentClient` memo was deleted. My own probe used the full WorkflowStepper and failed every Medical History GET. The banner still showed "Lidocaine", and the signed PUT `checkboxes` were `{"pregnant":"no"}`, with no allergy. The checked-in failed-fetch, pending and cached-refetch-fails tests also pass.
- r2 P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36 closed: `setTemplateFieldValue` is the only user writer of fieldValues (consent-step.tsx:228, called from both FormFieldRenderer onChange sites, lines 668 and 801). It adds the field id to `touchedFieldsRef` before writing, and an empty value counts. mergeGeneratedPrefill skips every touched id first (`if (touched.has(fieldId)) continue;`). Part B below is the reproduction.
- r2 P3 pages/api/clients/import/batch.ts:418 closed: the `smoking: false` default is gone. tests/integration/api-routes/client-import.test.ts asserts `historyInserts[0].condition` `not.toHaveProperty("smoking")`, so restoring `smoking:false` fails that assertion.
- r1 P1 components/appointments/workflow-steps/consent-step.tsx:256 closed: cached history is gated out by `isFetchedAfterMount` combined with `staleTime: 0`. Untouched generated answers are refreshed or cleared through `generatedPrefillRef`, and edited answers are never overwritten. These tests pass: "waits for the current record instead of prefilling a stale cached one" (a stale pregnancy Yes is not kept; Smoker follows the fresh record), "does not prefill a cached record while an offline iPad holds the refetch", "clears an untouched prefill when a refreshed record drops it, but keeps typed answers", and "Edit (re-sign) prefills only from the record fetched when the editor opens".
- r1 P2 pages/api/clients/[id]/medical-history.ts:164 closed: the GET no longer injects `smoking: false`, and CSV import no longer writes it. consent-variables.ts:310 maps `undefined` to "" and prefill.ts:251 leaves the radio unset. The GET test "leaves smoking unset when the record never stored it" and the stepper test "leaves smoking blank and unanswered when the record never recorded it" (token `Smoking=;`, both radios unchecked) both pass. Every other smoking reader handles absence safely: the medical-history form and step use `|| false`, contraindication-rules uses truthiness, and significant-summary and medical-history-guard use `=== true`.
- r1 P3 tests/components/appointments/workflow-consent-medical-history.test.tsx closed: I mutated `use-consent-client.ts`, replacing the `!res.ok` throw with `return { medicalHistory: {}, exists: false }`, through a scratch moduleNameMapper config with no tracked file touched. Three tests then fail: "treats a failed history fetch as unknown…", "does not fall back to a cached record when the refetch on open fails" and "pre-answers nothing when the refetch after a Medical History step save fails".
- r4 P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36 closed: this is the same defect as r2 F2, and the evidence is the same. I also mutated the code to remove the `touched.has` guard, again through a scratch mapper. Two tests then fail: consent-step-cleared-prefill "signs with Medication empty when the record lands after the patient cleared it", and workflow-consent "never refills an answer the patient cleared while the record was loading". The fix therefore has regression protection.

PART B: r4's probe re-run at this tip

- The probe used the full `WorkflowStepper` on the protocol Consent step, with the `/api/clients/client-1/medical-history` GET held behind a gate. It typed "Sertr" into Current medication, cleared it to "", then released the history response. That response had medications ["Sertraline"], allergies Lidocaine, smoking true and contraindications ["Pregnancy"]. The probe waited for Allergies to prefill to "Lidocaine", drew a signature, ticked "I have read and understood", and pressed Confirm & Sign.
- Signed PUT `data.checkboxes` = `{"medication":"","pregnant":"yes","smoker":"yes","allergies":"Lidocaine"}`. Medication is empty, the body contains no "Sertraline" anywhere, and the untouched fields took the fresh record.
- In a variant, a second history refetch (`invalidateQueries`) landed after the record and before signing. The PUT was identical and Medication stayed "".

PART C: adversarial pass on the full branch diff

- Shared query key: the contraindication checker, prescription form, client page and Medical History tab all cache the same `{ medicalHistory, exists }` shape under `["client", id, "medical-history"]`, so whichever observer's queryFn runs, the consent step reads the right shape. Swallow-on-error queryFns, such as the client page's `{ medicalHistory: null }` fallback, can only produce "nothing prefilled", never a fabricated answer.
- ProtocolConsentStep remounts per opening: there is an outer observer for the "Already signed" viewer, and an inner observer in ProtocolSignatureStep for the pending step, Edit (re-sign) and Re-sign. A record fetched while the "Already signed" panel was open cannot satisfy the inner observer's `isFetchedAfterMount`.
- `handleStepComplete` invalidates the history key before refetching the workflow. invalidateQueries does not throw on a failed refetch, so step completion is not broken by a history read error.
- mergeGeneratedPrefill: radios, text and arrays all compare correctly through `sameValue`. A generated field whose new prefill is empty is deleted. An existing non-generated value is never overwritten. The function is pure and safe inside the state updater.
- Gates I ran: the five touched suites pass, 73/73: consent-step-cleared-prefill, workflow-consent-medical-history, protocol-consent-resign-medical-history, the client-import integration suite and the medical-history integration suite. Full `tsc --noEmit -p .` with a 12 GB heap exited 0 with 0 errors.
- Checkout state: the probe test file was created and deleted, and mutation copies and the scratch jest config live only under /tmp/rev-3bm73-r5-opus-scratch. `git -C /tmp/rev-3bm73-r5 status --short` is clean.

UNVERIFIED

- Real iPad and browser interaction, window-focus refetch timing on Safari, and live DB persistence. Every HTTP boundary was mocked in jsdom.
- Legacy rows that store `smoking: null` in the condition JSONB would still render "Smoking: No" and preselect "no", because prefill.ts:251 and consent-variables.ts:310 test only `!== undefined`. No current writer produces null: the medical-history PUT writes `data.smoking || false`, and import now omits the key. Whether any such rows exist on prod was not checked, and the prior-default `false` rows are the pre-existing AestheticcNext-e5hw9.
- Full jest suite, the full pg lane, `npm run build` and lint were not run. Only the touched suites and tsc were run.
- If a patient signs before the slow Medical History read lands, the form's medical tokens are blank rather than "No". That is consistent with the bead outcome and with behaviour before this branch. Whether the signed record snapshots the resolved token text was not traced.

```review-verdict
{"verdict":"PASS","findings":[]}
```
