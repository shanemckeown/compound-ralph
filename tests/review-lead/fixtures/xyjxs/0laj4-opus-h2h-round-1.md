PASS
REVIEWING: goal/aestheticcnext-0laj4 @ 203110dbff50e40882f259d92d295423e0e5b86f

OPUS HEAD-TO-HEAD (own-gate, fresh context), code mode, bead AestheticcNext-0laj4. 0 P1, 0 P2, 0 P3.

Findings: none.

VERIFIED OK
- Proportionality (project CLAUDE.md "we are not the police"; TM-00/CLINIC_REALITY): the gap was reachable only by a hand-made POST from a staff login, since the importer is hidden from staff at pages/dashboard/business-studio/website/index.tsx:87. The fix is one line that reuses the existing guard and adds no new safeguard surface. It makes the route match the UI rule and the sibling portal-settings PATCH. I have no objection to building it. With no fix, no ordinary clinic scenario breaks, so a missing guard here would have been P3 at most. After the fix nothing is outstanding.
- Entry point to write trace: POST /api/website-import/apply → withApiWrapper(withThirdPartyDeniedAuthForMethods(handler)) (pages/api/website-import/apply.ts:136) → method check :59 → getUserBusinessId :63 → requireClinicAdmin({id, businessId}) :69 (returns before body parse) → applyImport :115 (the only write to portal_settings.website_config on this route). The guard sits before every write and before parsing, so a staff caller gets 403 and nothing is written.
- H-T1 (TM-01): the server-side owner/admin check is now present. lib/auth/clinic-admin.ts:43 requireClinicAdmin → canManageClinicSettings :29. Rule: super admin only while impersonating exactly businessId (team-utils isImpersonatingBusiness, :119-122). Otherwise isBusinessOwnerOrAdmin → getUserBusinessRole (lib/db/team-utils.ts:196-233): business_profiles.user_id owner first, then a non-deleted team_members row with role admin. Staff, third_party and anyone else get 403 "Only the clinic owner or an admin can do this".
- Owner regression check: an owner with no team_members row still passes, because the business_profiles.user_id match comes first (team-utils.ts:201-210). Owner/admin import is unchanged.
- Same businessId everywhere: the guard checks the businessId from getUserBusinessId, which resolves to the impersonated business for a super admin (team-utils.ts:130-135). applyImport writes to that same businessId. No mismatch between the checked clinic and the written clinic.
- Other writers of website_config: pages/api/portal-settings/index.ts PATCH is already requireClinicAdmin-guarded (sd1qc). pages/api/starter-images.ts only reads websiteConfig and is GET-only (:313). pages/api/public/gallery.ts is a public read. pages/api/website-import/crawl.ts only fetches the remote site and writes nothing to the clinic. applyImport has no other API caller (grep: only pages/api/website-import/apply.ts plus lib/website-import/apply.ts and the client component).
- H-U0 / H-U2 (UI-01, UI-07): the "Import pages" promise is unchanged for the people who see it: owner, admin, and super admin (UI canModifyBusinessSettings, lib/auth/permissions.ts:22). Staff never see the control, so no shown control 403s. Edge case: a super admin who is NOT impersonating sees the importer but now gets 403. That matches the WebsiteSettingsSection save beside it (portal-settings PATCH, same guard), and it only affects QA/super-admin accounts. No clinic scenario, so no finding.
- H-T3: no new query. The role lookups are scoped by (userId, businessId).
- H-D0: no duplicate path. It reuses lib/auth/clinic-admin.ts:43 as the plan's EXTEND line states.
- H-D1 / DR-06 non-vacuous: I ran this in a scratch worktree at 203110dbff, outside the repo (node_modules symlinked). Baseline: 4/4 pass. Mutant: delete the requireClinicAdmin line → "refuses a staff member's import with 403" FAILS, and the owner, admin and impersonating-super-admin controls stay green (1 failed, 3 passed). Controls are green both ways. Surviving mutants: none of the guard mutants I tried survived. The staff test also asserts isBusinessOwnerOrAdmin("user-7","biz-1"), which pins the checked clinic. The scratch worktree was removed afterwards and the review worktree is clean.
- H-D2: the test drives the real default-export handler and the real lib/auth/clinic-admin.ts. Mocked: the team-utils DB lookups, applyImport, the wrapper and third-party passthroughs, and logger. That is acceptable for a role guard because there is no SQL decision in the new code. The role SQL is unchanged and already in use by portal-settings.
- H-D3: fixtures are realistic (a one-page home import).
- H-D4: tests/api/website-import/ is not in jest.config.js testPathIgnorePatterns (grep "website" in jest.config.js: no hits). The suite ran under node-tests in the QA log.
- Evidence artifacts (all three cited): /Users/shane/.claude/evidence/goal/0laj4-20261024/qa-command-round-1.txt (tsc-heavy --noEmit && npm run lint && targeted-tests.py), /Users/shane/.claude/evidence/goal/0laj4-20261024/qa-output-round-1.log (lint warnings only, all pre-existing in unrelated files; PASS node-tests apply-admin-only 4/4; 0 related pg suites), /Users/shane/.claude/evidence/goal/0laj4-20261024/qa-exit-code-round-1.txt (0).
- Landing check: git merge-tree --write-tree of local origin/main (22b716a828) and 203110dbff merges clean (tree 6b3185a13b). The known file-length ratchet red for pages/api/website-import/apply.ts (136 vs 132) is left to the lander under house rule. It is not a finding.
- Not applicable: H-M1–M7 (no money), H-P0–P3 (no patient data or third-party egress), H-C1–C4 (no sends), H-B1–B6 (no booking), H-T2 (no new clinic ability), H-U1, H-U3, H-D5–D7 (no timestamps, no migration).

UNVERIFIED
- git fetch origin main was blocked by the sandbox (github.com:22), so the merge-tree ran against the local origin/main ref, which may be stale. I did not check other pushed-not-landed branches for apply.ts overlap (the worktree guard refused the loop).
- I did not drive the app in a browser (no /browse run). The owner/admin import path is verified by the handler test and code trace only.
- The real role SQL was not run on pg. It is unchanged code shared with portal-settings PATCH.

```review-verdict
{"verdict": "PASS", "findings": []}
```
