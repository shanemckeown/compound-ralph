REVIEWING: goal/aestheticcnext-9kwlw @ 5f45b3c9d2812c0ba70556c3279fbbff8ab3f6db
FAIL
P2 lib/utils/google-speech.ts:219 — DR-08, H-U0: a practitioner finishing a three-minute note on an iPad during Google unavailability can exceed Cloud Run’s 300-second deadline. The inherited short path starts batch recognition without timeout options; its 240-second timer starts only after that RPC returns. Reproduced still pending after 300001 ms, leaving a pending recording outside reconciliation — enforce an overall speech deadline below 300 seconds, including startup, inline recognition and SDK retries.

VERIFIED OK

- 173 assertions passed across 15 targeted suites, including six PostgreSQL checks. Four confirmed mutations failed with controls passing: failed-status warning, safe reason, SKIP LOCKED and lease. DR-01, DR-06; H-D1, H-D2, H-D3, H-D4, H-D5.
- Terminal writes retain tenant-scoped CAS with RETURNING; only the winner persists text/metadata and emits the finish log. Spelling preparation is local. Reconciliation contains no credit calls. TM-01, TM-04, COM-02; H-T3, H-M4.
- Background startup has a 60-second SDK budget; lookup returns within 10 seconds, with the installed SDK’s effective lookup retry budget at 60 seconds. Sweep runs after automation recovery and stops starting checks after its 60-second budget.
- Recorder cap, pending/completed/failed states, deletion handling, failure warnings and short-note regression checks pass. UI-05, UI-07, COM-06; H-U0, H-U1, H-U2.
- H-D0: PLAN.md:26 records a valid refused-extend reason: ordinary recording fetches remain free of Google reconciliation calls. The separate poll route is justified.
- Patient boundaries, safe error handling and existing storage/deletion paths checked. TM-07; H-P0, H-P1, H-P2, H-P3. Lease timestamps use explicit ISO/timestamptz handling; no migrations. DR-02; H-D6, H-D7.
- H-C4 reuses the existing scheduler route. H-M1–H-M3, H-M5–H-M7, H-T1, H-T2, H-C1–H-C3, H-B1–H-B6 and H-U3 are not applicable to the changed workflows.

UNVERIFIED

- Real iPad bitrate, hour-long Google decoding/result size, current scheduler activation, and production 20/55-minute transcript/playback acceptance within ten minutes. UI-01, COM-07, DR-07.

```review-verdict
{"verdict":"FAIL","findings":[{"severity":"P2","file_line":"lib/utils/google-speech.ts:219","text":"DR-08, H-U0: a practitioner finishing a three-minute note on an iPad during Google unavailability can exceed Cloud Run’s 300-second deadline. The inherited short path starts batch recognition without timeout options; its 240-second timer starts only after that RPC returns. Reproduced still pending after 300001 ms, leaving a pending recording outside reconciliation — enforce an overall speech deadline below 300 seconds, including startup, inline recognition and SDK retries."}],"prior":[{"ref":"r1 P2 hooks/useVoiceInput.ts:545","status":"closed","evidence":"Verified safe background-start failure reasons reach the upload response and produce warning feedback. Targeted handler and hook tests pass. Confirmed compiler mutations removing the safe reason or failed-status handling fail their regression tests while controls pass."},{"ref":"r1 P2 pages/api/voice-notes/[id]/transcription.ts:38","status":"closed","evidence":"Read PLAN.md:26 and traced both endpoints. The recorded refused-extend reason is valid: plain recording GET remains free of Google reconciliation calls, while the explicitly polled route reconciles under the authenticated clinic and third-party gate. I agree with the dismissal."},{"ref":"r1 P3 lib/voice-notes/transcription-reconcile.ts:207","status":"closed","evidence":"Verified the atomic UPDATE with FOR UPDATE SKIP LOCKED and four-minute lease on real PostgreSQL. All six sweep checks pass, including overlapping sweeps, locked-row skipping, expired leases and tenant-scoped CAS. Confirmed mutations removing SKIP LOCKED or lease exclusion fail the relevant concurrency test; controls pass."}]}
```
