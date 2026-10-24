# Review AestheticcNext-3bm73 — ASTRA XHIGH — 2026-10-23 (tip 302cd5a79eb4911dc07255d890ff90b2761a4762)

Verdict: FAIL

FAIL
REVIEWING: goal/aestheticcnext-3bm73 @ 302cd5a79eb4911dc07255d890ff90b2761a4762

P1 components/appointments/workflow-steps/consent-step.tsx:256 — A practitioner updates a returning patient’s history on their phone, then opens protocol consent on the clinic iPad with older cached history. Cached answers are prefilled immediately; after fresh history arrives, the tokens update but the answers remain stale because lines 259–273 preserve every nonempty value. Reproduced pregnancy “Yes” remaining after its indication disappears, smoking “No” remaining when current history says “Yes”, and submission containing the stale pregnancy answer — defer medical prefill until the current fetch succeeds, and distinguish generated answers from patient edits so refreshed history replaces or clears untouched prefills. [TM-07, DR-05, UI-04, H-U0]

**VERIFIED OK**

- Inspected the exact requested SHA; tracked checkout remains clean.
- Eight exact-path suites passed: **106 tests**, covering the four requested component suites, pregnancy helpers, medical-history API and file-length ratchet.
- **H-D0:** reuses the existing GET, `buildFormPrefill` (`lib/forms/prefill.ts:33`) and `buildConsentVariableValues` (`lib/forms/consent-variables.ts:265`). The hook extracts the existing demographic builder; it adds no competing prefill implementation.
- **H-T3 / TM-01:** the GET first authorizes `clientId + businessId` in SQL (`pages/api/clients/[id]/medical-history.ts:41`), then reads history through that client’s FK. Dedicated allergies are also business-scoped. Query keys include the workflow’s client ID; the independent client-switch probe passed.
- **H-U0:** without cached history, pending requests, HTTP 404/500, empty history and unknown pregnancy leave pregnancy unanswered and its token blank. Recorded pregnancy/breastfeeding produces “Yes”; explicit legacy false produces token “No” without pre-answering the question. This matches the shared helpers used by the client-page path.
- **H-P0/H-P1 / TM-07:** no new logging, analytics or Sentry payloads; the new fetch error contains no patient values. **H-P2:** no practitioner-internal notes source added. **H-P3:** no new storage.
- **H-B2:** extracted demographic mappings retain main’s defaults; allergy fallback remains. Create/edit paths receive the same client object, and existing edit/redo and client-mode suites passed.
- **H-D2/H-D3/H-D4:** the new suite renders the real stepper and consent components with mocked HTTP; fixtures use the actual contraindications shape. The suite is active, not quarantined.
- File ceilings pass: hook **77/350**, stepper **1758/1786**, consent step **940/940**.
- Main landing check: **no conflicts**. This branch changes no `VERSION`, `CHANGELOG.md`, baseline or migrations. Other unmerged refs conflict in touched components: `auto/AestheticcNext-wcnrn`, `auto/AestheticcNext-wcnrn-2`, `auto/AestheticcNext-x8ejl`, and `shanemckeown/treatment-search`. Those comparisons also contain `VERSION` collisions; no CHANGELOG, baseline or migration collisions were found.
- Not applicable: **H-M1–M7, H-T1–T2, H-C1–C4, H-B1/H-B3–B6, H-U1–U3, H-D6–D7**. No payment, communications, settings, booking-rule, alert, timestamp or migration change. [COM-01–07, DR-02–03]

**UNVERIFIED / EVIDENCE LIMITS**

- **H-D1 / DR-06:** 15 mutants tested; **9 killed, 6 survived**. Pregnancy, conditions, medications, smoking, prefill, late-arrival dependency, completion invalidation, null-client guard and envelope extraction mutations went red. The independent client-mode submission control passed baseline and every mutant.
- Surviving mutants: fallback-only allergies; HTTP failure returned as empty history; unconditional query enablement; removal of client ID from the query key; unconditional history-object construction; weakened allergy-array guard. The checked-in suite therefore does **not** establish those protections. The unconditional empty object is output-equivalent in these fixtures.
- **H-U0/H-D5:** independent scratch probes finished **12 passed, 2 failed**; both failures demonstrate the stale-prefill finding. A separate passing diagnostic confirmed the stale pregnancy answer reaches the submission payload.
- Real-browser/iPad interaction, live PostgreSQL isolation, persisted consent readback and production telemetry were not exercised. API tests mock the database. No full pg lane or Cloud Build simulation ran. [UI-01, DR-07]

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P1","text":"components/appointments/workflow-steps/consent-step.tsx:256 — A practitioner updates a returning patient’s history on their phone, then opens protocol consent on the clinic iPad with older cached history. Cached answers are prefilled immediately; after fresh history arrives, the tokens update but the answers remain stale because lines 259–273 preserve every nonempty value. Reproduced pregnancy “Yes” remaining after its indication disappears, smoking “No” remaining when current history says “Yes”, and submission containing the stale pregnancy answer — defer medical prefill until the current fetch succeeds, and distinguish generated answers from patient edits so refreshed history replaces or clears untouched prefills. [TM-07, DR-05, UI-04, H-U0]"}]}
```
