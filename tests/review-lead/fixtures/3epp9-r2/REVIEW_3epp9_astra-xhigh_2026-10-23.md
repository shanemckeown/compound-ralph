REVIEWING: goal/aestheticcnext-3epp9 @ 411ca87101777a35cd63fb6e80642eab4faae0fb
FAIL
P2 components/clients/overview-tab.tsx:348 — Between appointments, a practitioner on an iPad stops A’s memo, opens cached B while transcription runs, then returns to A within a minute: A’s successfully saved memo remains absent because unmount suppresses onComplete and its cache invalidation (DR-05, UI-07, H-U0) — invalidate the captured client’s memo cache through the existing teardown-safe onUploadSucceeded callback and add a regression using production cache settings.

VERIFIED OK

- **Actual implementation:** there is no `VoiceInput` key at this tip. The synchronous client-change reset at `overview-tab.tsx:345` closes the recorder, causing unmount.
- **Pending microphone start:** unmount invalidates the start token; resolving permission afterwards stops the stream without creating a recorder or uploading (`useVoiceInput.ts:179`, `:234`).
- **Live recording:** unmount calls `stop()` without removing the recording’s handlers. Both immediate and browser-queued stop checks upload A’s audio with A’s ID.
- **Complete identity trace — TM-01, TM-07:** Overview supplies A in `metadata.clientId` (`overview-tab.tsx:875`). Starting captures that render’s configuration. The installed `onstop` retains its `processRecording` closure (`useVoiceInput.ts:258`), which sends A in both metadata and the separate form field (`:490`). This call site supplies no stop-time metadata getter.
- **Server trace:** `/api/audio/process` reads A from the submitted form (`process.ts:186`), validates A against the authenticated business (`:193`), and inserts `audioRecordings.clientId=A` (`:364`, `:391`). Transcription updates that newly generated recording ID (`:446`); it never reads the currently displayed client. An upload already underway therefore continues against A intentionally.
- **Delayed completion isolation:** A’s response cannot close B’s newly started recorder or display A’s transcription on B.
- **Route reuse:** both desktop and mobile render unkeyed Overview instances (`pages/dashboard/clients/[id].tsx:1179`, `:1332`). Cached navigation can reuse them; the new reset handles that case. Loading branches unmount them (`:1010`).
- **Drafts:** switching discards unsaved note/label input and loads B’s values. These controls require explicit Save/Add; no autosave or navigation-preservation promise is present. The label test confirms B’s PATCH contains only B’s labels.
- **Existing targeted checks:** all three named component suites passed: **11 tests**.
- **Finding reproduced:** with production `staleTime: 60000`, the delayed-upload return-to-A check fails. Removing the new recorder reset makes it pass. An in-memory `onUploadSucceeded` cache callback makes all four supplemental checks pass. That repair is **not applied to the branch**.
- Worktree remained clean; mutations and supplemental tests existed only in memory.

UNVERIFIED

- Real microphone/browser behavior, actual Next.js route navigation, storage upload, PostgreSQL persistence, and Google transcription were not executed. Server conclusions above are source traces; HTTP was mocked.
- Existing check not used: the bead’s `tsc` acceptance check was not rerun.
- Other unlanded branches were not checked for pairwise conflicts.

Hazard answers

- **H-P1 / H-P0 — TM-07:** no new logging, analytics, or vendor payload path. This is a changed-code assessment, not a complete telemetry audit.
- **H-T3 — TM-01:** no new query; existing upload ownership validation scopes A by client ID and authenticated business.
- **H-D0:** no duplicate recorder or persistence path. Existing `useVoiceInput.ts:129` and `pages/api/audio/process.ts:67` are reused. `formatDob` was moved, with its original implementation removed.
- **H-D1 — DR-06:** all seven tested mutations were killed; the same-client control passed with each mutation.
- **H-D2:** tests drive real Overview, VoiceInput, and hook code. Browser APIs and HTTP are mocked; they do not prove database persistence.
- **H-D3:** clinical fixtures are realistic. The supplied live-recording test sends zero duration to an accepting mock; supplemental lifecycle checks used five seconds, above the server minimum.
- **H-D4:** the suite is active in `dom-tests`, not quarantined; executed counts confirmed.
- **H-D5 — DR-07:** builder PASS claims were independently rechecked. The additional failing case remains a finding.
- **H-U0 — UI-07, DR-05:** recording identity is preserved; the displayed memo list breaks the saved-result promise described above.
- **H-U2 — UI-01:** Start/Stop, label Add, and memo Play paths passed component checks; real-device verification remains unverified.
- **H-C4:** no new activation requirement; existing clinic settings remain in use.
- **Not applicable to this diff:** H-M1–H-M7, H-T1–H-T2, H-P2–H-P3, H-C1–H-C3, H-B1–H-B6, H-U1, H-U3, H-D6–H-D7: no corresponding money, settings, sending, booking, alert, timestamp, or schema change.

Mutation table

Each run used only the client-switch test file. Cache persistence was disabled in memory because the initial ordinary Jest invocation hit sandbox EPERM.

| Neutralized change | Failing tests | Same-client control | Result |
|---|---:|---|---|
| `setShowVoiceInput(false)` | 2 | Pass | Killed |
| `setIsEditingNotes(false)` | 1 | Pass | Killed |
| Notes-value reset | 1 | Pass | Killed |
| Labels reset | 1 | Pass | Killed |
| Label-draft reset | 1 | Pass | Killed |
| Entire client-change condition disabled | 4 | Pass | Killed |
| `setStateClientId(client.id)` | 4, render-loop errors | Pass | Killed |
| Recorder key | — | — | Not present at this tip |

**Surviving mutants: none.**

Landing check

Ran `git merge-tree $(git merge-base origin/main HEAD) origin/main HEAD` against local `origin/main` at `c21bb35be9a607f57b9998522a22b5beb8696162`.

**Conflicts: none.** No VERSION, CHANGELOG, baseline, or migration changes in this diff.

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P2","text":"P2 components/clients/overview-tab.tsx:348 — Between appointments, a practitioner on an iPad stops A’s memo, opens cached B while transcription runs, then returns to A within a minute: A’s successfully saved memo remains absent because unmount suppresses onComplete and its cache invalidation (DR-05, UI-07, H-U0) — invalidate the captured client’s memo cache through the existing teardown-safe onUploadSucceeded callback and add a regression using production cache settings."}]}
```
