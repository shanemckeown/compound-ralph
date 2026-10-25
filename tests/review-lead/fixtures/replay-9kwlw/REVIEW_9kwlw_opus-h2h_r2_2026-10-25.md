REVIEWING: goal/aestheticcnext-9kwlw @ 5f45b3c9d2812c0ba70556c3279fbbff8ab3f6db
PASS

No new findings at this tip.

VERIFIED OK
- Opus h2h seat, independent round 2, fresh context. Read only my own round-1 file and Astra's round-1 file. Read no round-2 output from any other seat. Checkout detached at 5f45b3c9d2 and clean at the end: the scratch test file and the node_modules link were removed, and every mutant was reverted with git checkout.
- Entry point 1, POST /api/audio/process: unchanged gates (business resolved server-side, third-party gate, premium gate, charge once at upload). transcribeUpload now returns transcriptionError only when a long note's start returned 'failed'. That covers both the missing-GCS fail and the start error or timeout fail inside startBackgroundTranscription. The text is the fixed BACKGROUND_START_FAILED_MESSAGE, and Google's own text stays only on the row's metadata. The route spreads transcriptionError into the response for both the credit refusal and the start failure. The short path (270 s or less) is unchanged and returns no reason.
- Entry point 2, GET /api/voice-notes/[id]/transcription: unchanged since round 1. The recording read is scoped by id plus req.businessId, the third-party gate runs first, and it calls the one reconcile implementation. It ignores the sweep's claim on purpose. The CAS keeps that race to a single write (scratch S2 below).
- Entry point 3, stale-attempt-sweeper cron: the sweep is still called after the automation recovery and the rollup, inside the try. It never throws: the claim's own error lands in the outer catch, which logs only the error class and returns 0.
- New claim (transcription-reconcile.ts claimWaitingRecordings) is a single autocommit statement: UPDATE ... WHERE id IN (SELECT ... 'processing' AND has an operation AND (no claim OR claim older than 4 min) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 20) RETURNING. The claim key is written with jsonb_set on the locked current value, so it cannot clobber other metadata keys (scratch S5). The only other request-path writer of audio_recordings is the PATCH in pages/api/voice-notes/[id]/index.ts. It writes title and updatedAt only, never metadata, so nothing races the claim for metadata. No trigger and no RLS on audio_recordings in any migration.
- Real-pg scratch file (testcontainers, separate connections, pool max 4; deleted afterwards), 5/5 green:
  - S1: a poll holds the row mid-finish in an open transaction. The claim skips it in under 3 s without asking Google, and after the commit no tick asks Google again.
  - S2: the cron claims the row and is waiting on Google, and the recorder's poll finishes the row first. The sweep's CAS loses and returns the stored row. There is exactly one finished log, and the poll's words are kept.
  - S3: three ticks race over 10 rows in 2 clinics. Every operation is checked exactly once.
  - S4: rows run oldest first across day and year boundaries. createdAt comes back from the postgres-js driver as a string, so the sort is correct.
  - S5: completed rows and legacy rows with no operation are never claimed, and existing metadata keys survive the claim.
- Builder's real-pg file tests/pg-integration/background-transcription-sweep.test.ts: 6/6 green.
- Mutations (the builder's pg suite unless stated):
  - M1, FOR UPDATE without SKIP LOCKED: RED (the "skips a recording another tick is locking" test times out at 5 s).
  - M2, lease filter removed: RED (overlap test and lease test).
  - M3, lease set to 0: RED (overlap test and lease test).
  - M4, lease 6 min, longer than the 5-min cadence: RED (lease test).
  - M5, the oldest-first sort after RETURNING removed: builder suite green, my S4 RED. This only affects ordering within a tick under budget pressure, so it is not raised.
  - M6, hook backgroundFailed reset to false: RED (use-voice-input-background-transcript "no reason still warns").
  - M7, transcriptionError dropped from transcribeUpload: RED (2 integration tests).
  - M8, the route spreading the credit refusal only: RED (2 integration tests).
  - M9, credit-refusal reason dropped from the destructure branch: RED.
- Must-hold, CAS: the finishing write is still UPDATE ... WHERE id, business_id and transcription_status 'processing' RETURNING. A loser re-reads by id plus business_id, and the finished log runs only for the winner (scratch S2 confirms on real pg).
- Must-hold, no CreditService in any reconcile path: grep over lib/voice-notes, the poll route and the cron finds no credit-service import.
- Must-hold, sweep time-boxed and after recovery: SWEEP_BUDGET_MS 60 s, checked before each row; call order is unchanged. The lease (4 min) is longer than one tick (60 s budget plus one 10 s check) and shorter than the 5-min cadence. A row claimed but not reached waits one tick, never longer.
- Must-hold, Google bounds: START_TIMEOUT_MS 60 s, raced and passed as the gax timeout, which bounds SDK retries. CHECK_TIMEOUT_MS 10 s, raced. The delta does not touch either.
- H-U0 on every touched string:
  - Start failed: "Recording saved. Not transcribed: it could not be sent to the transcription service. You can still play it back." This is true: the row is 'failed' and the audio is stored with a signed playback URL in that branch.
  - Failed with no reason (a short note whose sync transcription failed): "Recording saved, but it could not be transcribed. You can still play it back." This is true. It replaces main's neutral "Recording saved to library", which hid the failure. That is the only change for notes under 4.5 minutes, and it is more truthful.
  - Credit refusal: still shows its own reason.
- Targeted tests: all 11 changed non-pg test files green (139/139). The pg file is green as above.
- Hazards:
  - H-T3 yes: the claim is cross-tenant by design, the same as the cron, and each row is then written under its own business_id.
  - H-P1 yes: the claim carries only ISO timestamps as parameters, and the outer catch logs the class only.
  - H-U0 yes, as above.
  - H-D0 yes: no new path. The claim replaces the old SELECT in place, following the lib/webhooks/delivery-worker.ts idiom.
  - H-D1 and H-D2 yes: mutations plus real pg, above.
  - H-D6 yes: ISO strings with ::timestamptz casts, and the test runs TZ=UTC.
  - H-C4 yes: no new scheduler.
  - H-D7 n/a: no migration; the claim key sits in the existing metadata jsonb.
  - Money, booking and comms hazards (H-M*, H-B*, H-C1..C3, H-P2): not applicable, nothing touched.
- Rules: TM- tenancy holds (every request-path query is business-scoped). DR- every row still reaches a terminal state, and the claim cannot strand a row beyond one lease. COM- not applicable. UI- copy truthful as above.

UNVERIFIED
- Real Google SDK behaviour stays mocked in every test, as in round 1. Prod acceptance with 20- and 55-minute notes on Demo Clinic is what proves it.
- A settled row keeps the transcriptionSweepClaimedAt key in its metadata. This is harmless: no reader of metadata uses it, and the claim filter only looks at 'processing' rows. Noted, not raised.
- Safari or iPad bitrate for a 60-minute note, carried from round 1.

```review-verdict
{"verdict": "PASS", "findings": [], "prior": [{"ref": "r1 P2 hooks/useVoiceInput.ts:545", "status": "closed", "evidence": "At 5f45b3c9d2 transcribeUpload returns BACKGROUND_START_FAILED_MESSAGE when a long note's start lands 'failed' (no GCS URI, or start error or timeout), process.ts spreads transcriptionError into the response, and useVoiceInput seeds backgroundFailed from status 'failed', so the practitioner gets toast.warning with the plain reason, never toast.success. Mutants M6 (backgroundFailed false), M7 (reason dropped in transcribeUpload) and M8 (route spreads the credit refusal only) each turn a test RED."}, {"ref": "r1 P2 pages/api/voice-notes/[id]/transcription.ts:38", "status": "closed", "evidence": "I agree with the recorded refusal at PLAN.md:26. Extending GET /api/voice-notes/[id] would put a Google call and a write on every plain fetch of a note (mobile app, NoteTranscriptLink), and that GET has no side effects. The poll route reads the same business-scoped row (id plus req.businessId, third-party gate first) and calls the one reconcile implementation, so no job is implemented twice. Not an H-D0 duplicate."}, {"ref": "r1 P3 lib/voice-notes/transcription-reconcile.ts:207", "status": "closed", "evidence": "c47344d82f replaced the plain SELECT with one UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 20) RETURNING claim, with a 4-minute lease in metadata, before any Google call. Real pg, separate connections: a row locked by an in-flight poll is skipped without waiting; three racing ticks check each of 10 rows exactly once; a lapsed lease is re-claimed. Mutants without SKIP LOCKED, without the lease, with a 0 lease and with a 6-minute lease each turn the builder's pg suite RED."}]}
```
