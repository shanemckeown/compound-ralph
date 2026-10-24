REVIEWING: goal/aestheticcnext-8mpih @ cfc805e50567d0ccaea4f2e303ed82c962432761
PASS
Reviewer: fresh Opus 5.5 head-to-head, independent round 4, 2026-10-24.

Scope: `git diff af5df710da cfc805e505` (lib/ava/turn.ts, lib/ava/unpaid-reply.ts, scripts/eval/ava/live.mjs, tests/unit/ava/unpaid-reply.test.ts), against the REFLECTION before attempt 4 on AestheticcNext-8mpih. Personas (TM-, COM-, DR-, UI-) and CLINIC_REALITY read. Hazards applied: H-U0, H-D1, H-D2, H-D5, H-D0. H-M*, H-T*, H-P*, H-C*, H-B* do not apply: this is a read-only reply-shaping change with no new query, send, payment or setting.

## Findings

P3 lib/ava/unpaid-reply.ts:96 — the owner asks on her phone between clients, "who owes me money and what's on today?". unpaid_balances returns [] and get_diary returns Amy P. (lip filler, price "60.00"). If Gemini then invents "Amy P. owes £60" (the figure is sourced from the diary row) or "Amy P. still owes you" (no figure), the figure guard passes it, and the reply reads "Amy P. owes £60. … Nobody owes you anything for bookings from 24 September to 24 October." I reproduced both with a scratch FakeAvaModel test. The REFLECTION's test 4 aim ("the fixed line never sits beside an invented debt") holds only for an amount no read supplied, which is what the test at tests/unit/ava/unpaid-reply.test.ts:465 covers. The builder disclosed the name-only case; the amount-sourced-from-another-read case was not disclosed. This is not a regression: it needs a model hallucination against an explicit `owing: []`, and without the appended line the invented debt would stand uncontradicted. Fix: file a follow-up bead. When every unpaid read was empty, run the money figures in the reply through a guard whose sources are only the unpaid reads, or refuse a reply that pairs a client name from another read with "owe". Not this branch.

## VERIFIED OK
- r3 P2 lib/ava/unpaid-reply.ts:88 closed: saysDays is deleted, so `grep -rn saysDays lib scripts tests` is empty. withCheckedDays (unpaid-reply.ts:92-100) no longer reads the model's text: it checks only `unpaid.length` and `unpaid.every(Boolean)` and always appends `nobodyOwes(unpaid).reply`. The tests at unpaid-reply.test.ts:419 ("on 31 August" after 1-31 Aug) and :444 (undated on 31 Oct naming only "31 October") assert the full dated line is appended, and both pass.
- No text matching on model output remains in the reply path. A grep of turn.ts, unpaid-reply.ts and model-call.ts for `.includes/.match/.test/RegExp` on the reply finds nothing. The only check on the model's text is the existing figure guard, `unsupportedFigures` at turn.ts:103.
- The figure guard runs before the append: turn.ts:103-105 runs `unsupportedFigures(reply, sources)` and then `withCheckedDays(reply, unpaid)`. Mutant M7 moved the guard after the append and survived, but it is equivalent: the appended line's dates come from figureInput, which is already in sources. Removing the guard (M8) turns three tests red, including the test-4 case at :465.
- The fixed line names exactly the windows checked: `nobodyOwes` joins `[...new Set(unpaid)]`, and every unpaid entry is the emptyWindow label of a read that came back empty. The append happens only when all entries are non-null, so a window that found a debtor or whose read failed is never named. The multi-window label is covered at :201 and :251.
- All six required tests exist and assert exact output: (1) :419 1-31 Aug alone; (2) :430 compound with find_gaps, exact `${say} ${augLine}`; (3) :444 undated on 31 Oct with an exact 1-31 October line; (4) :465 invented £60 → figureGuard, with no "Nobody owes" (see the P3 for its limit); (5) :478 a debtor or a failed read gives `toEqual` the unchanged reply and outcome "answered"; (6) :502 a payment-link question with no read keeps its text, followed by the line.
- Mutants on unpaid-reply.ts, run against the four ava suites that import it (121 tests). All were killed: M1 replace instead of append (8 red); M2 no append, outcome kept (8 red); M3 `every`→`some` in the empty-only condition (4 red); M4 `!unpaid.length` dropped (20 red); M5 line before the reply (7 red); M6 condition removed (31 red).
- live.mjs:71-75 now requires `outcome === want` and, for nobodyOwes cases, a reply ending in /Nobody owes you anything for bookings (from|on) [^.]+\.$/. The bare-"answered" acceptance is gone.
- The `nobodyOwes` outcome's only consumer is `recordAvaEvent` at pages/api/ava/message.ts:122. Returning the model's text plus the line under outcome nobodyOwes changes no UI branch.
- Tests: `jest tests/unit/ava` 17 suites, 343 passed. `--findRelatedTests lib/ava/unpaid-reply.ts lib/ava/turn.ts` 5 suites, 137 passed. `RUN_PG_TESTS=true jest --selectProjects pg-tests --runTestsByPath tests/pg-integration/action-reads-ava.test.ts` 1 suite, 3 passed.
- tsc: `NODE_OPTIONS=--max-old-space-size=8192 npx tsc --noEmit` exit 0.
- Landing: `git merge-tree --write-tree origin/main cfc805e505` is clean (exit 0, tree 7b1329902a). origin/main is 23 commits ahead. VERSION, CHANGELOG, baseline and migration files are untouched in this round's diff.
- H-D0: no new path. The change deletes code in unpaid-reply.ts and simplifies the withCheckedDays signature (askedOther removed).

## UNVERIFIED
- Live Gemini behaviour at this tip (the builder reports 14/15, with the miss on cb4oh MAX_ROUNDS). Not re-run here.
- Other pushed-not-landed branches touching lib/ava/*: only the origin/main merge was checked.
- The duplicate-sentence polish (AestheticcNext-0qi2l), cb4oh and scrf9 are out of scope and were not reviewed.

```review-verdict
{"verdict": "PASS", "findings": [{"severity": "P3", "text": "lib/ava/unpaid-reply.ts:96 — owner on her phone asks 'who owes me money and what's on today?'; unpaid read empty, diary has Amy P. at £60; if Gemini invents 'Amy P. owes £60' (figure sourced from the diary) or a name-only debt, the figure guard passes it and the dated 'Nobody owes you anything' line is appended beside it (reproduced); test 4 covers only an unsourced amount — follow-up bead: when every unpaid read was empty, guard money figures against the unpaid reads only, or refuse client-name + 'owe' pairings"}]}
```
