---
name: check-frontend
description: Drive the real Aestheticc app on prod Demo Clinic and report what's broken, like a user would find it. Use when Shane or the orchestrator says "check the front end", "check the user experience", "click through the app", "use the app and see if X works", "does X work for staff / reception / patients", or after any user-facing feature lands. Default = the no-model crawler over every sidebar page, all roles, against the Feature Map. With a feature name = walk that feature end to end. Output is a short pass/fail list with repro steps. Replaces reaching for gstack /qa or /qa-only on Aestheticc (those are vendor skills, screenshot-heavy, and have no send/charge guard).
---

# /check-frontend

**Rules first:** read `~/Documents/Obsidian/Aestheticc/Reference/AGENT_BROWSER_POLICY.md` (tool choice + the 10 irreversible-button rules). Findings go through `Aestheticc/Ops/personas/CLINIC_REALITY.md`. Prod only on **Aestheticc Demo Clinic** `a4cc955f-7859-4dcc-bfb2-c0b28c0bba17`. Never press send / charge / pay / post / publish / sign / delete / confirm: the success state is "reached, not pressed".

## 0. Pre-flight (every run, ~1 min)
1. Test accounts exist (non-super_admin): `~/.gstack/sweep-role-credentials.json` (0600) holds `owner`, `staff`, `receptionist` for Demo Clinic. Missing → recreate via a DB insert on Demo Clinic only (bcryptjs cost 12, `users.role='user'`, `team_members.role`), record in the run's restore ledger.
2. Demo Clinic has an `automation_settings` row (a GET to `/api/automation-settings` inserts a reminder-enabled row if missing).
3. Demo Clinic has **future shifts** (`staff_shifts.shift_date >= today`) if the run touches booking; its own ran out 14 Sep 2026. Add TEMP shifts (tagged in `notes`) and delete them after.
4. Reuse saved sessions `~/.gstack/sweep-state-<role>.json`; log in only if `/api/auth/session` is empty (prod limits ~15 sign-ins/hr per IP).

## 1. Default: whole app, every role
```bash
cd <AestheticcNext checkout on main>
# per role (owner, staff, receptionist); creds via env only, never args/files in the repo
CRAWL_EMAIL=… CRAWL_PASSWORD=… npx tsx scripts/app-crawl/crawl.ts --role <role> \
  --storage-state ~/.gstack/sweep-state-<role>.json --out <scratch>/crawl-<role> \
  --feature-map <scratch>/feature-map.crawl.json
```
- **Use `npx tsx`, not `bun`** (bun crashes in Playwright's fetch after login).
- Feature map input: convert `Aestheticc/Product/feature-map.json` to `[{id, path, expect}]` (skip routes with `[id]`).
- Report: `failures[]` (dead-link / http-4xx / http-5xx / blank / console-error / no-op-button / feature-map-mismatch), `reachedNotPressed[]`, `guardAborted[]` (must be empty apart from telemetry).
- Managed visibility: repeat the staff crawl with Demo Clinic flipped to `team_visibility_mode='managed'`, restoring it in `finally`.

## 2. With a feature name: walk it
1. Find the feature's path in the Feature Map (`Sidebar → X → Y`, expect, pass, roles).
2. Walk it as each role in `roles`: text snapshot (URL + excerpt + safe element list, unsafe elements removed by code) → LLM picks the next click → code clicks → code checks the Feature Map's **pass** line. Jev only for narrow page judgments, not as the chooser (policy table).
3. Patient-facing feature (booking, forms, pay links, aftercare, portal): run on **iPhone 13 and 1440px**, headless, GET-only guard; mint test links directly in the DB (Demo only, tagged) instead of triggering a send.
4. A "does nothing" result must be confirmed with a real coordinate click before it's reported.

## 3. Output (keep it short)
```
CHECK-FRONTEND <scope> — <date> — <n> pass / <n> fail
FAIL  <role> <viewport>  <Sidebar → path>  <what happened vs Feature Map "pass">  repro: <steps>  evidence: <file>
PASS  …(one line each, or a count if many)
REACHED, NOT PRESSED: <buttons>
RESTORED: <every forced state, verified>
```
Real failures → beads (`AestheticcNext-`, `Outcome:` + `Premise:` + runnable check + file paths). Live P0/P1 at a real clinic → orchestrator immediately; everything else batched.

## Feature Map rule (keeps this useful)
Every new user-facing feature adds its entry to `Aestheticc/Product/feature-map.json` + `FEATURE_INVENTORY.md` (path, expect, pass, roles) in the same branch; `/goal` acceptance enforces it.
