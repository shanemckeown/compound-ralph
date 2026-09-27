// Offline tests for jev-stop-verify (LUCY-wow7r). No network, no key: fetch is mocked.
// Run: node --test ~/.claude/hooks/jev-stop-verify/test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { decide } from "../vendor/belay.mjs";
import {
  THRESHOLD, checkLabel, evidenceFromRecords, guardedFetch, isCodeChange, isTurnStart, isWorkerSession,
  guardAllows, isReadOnlyMention, isRealCheck, patientDataReason, redact, run, stateOf, taskOf, workerRecords,
} from "../hook.mjs";

const WORKER = "11111111-2222-3333-4444-555555555555";
const MANIFEST = `${JSON.stringify({ bead: "AestheticcNext-test1", session_id: WORKER })}\n`;
const CWD = "/Users/shane/Documents/GitReBase/AestheticcNext";
const ts = (i) => `2026-09-26T22:00:${String(i).padStart(2, "0")}.000Z`;
const bg = (rec) => ({ sessionKind: "bg", entrypoint: "cli", ...rec });

// Transcript builders, in the shape Claude Code writes for a --bg worker.
const goalPrompt = (i = 0) => bg({ type: "user", uuid: "u0", promptId: "p1", timestamp: ts(i), message: { role: "user", content: "<command-message>goal</command-message>\n<command-name>/goal</command-name>\n<command-args>AestheticcNext-test1</command-args>" } });
const meta = (i, text) => bg({ type: "user", isMeta: true, timestamp: ts(i), message: { role: "user", content: text } });
const toolUse = (i, id, name, input) => bg({ type: "assistant", timestamp: ts(i), message: { role: "assistant", content: [{ type: "tool_use", id, name, input }] } });
const toolResult = (i, id, { error = false, stdout = "" } = {}) => bg({ type: "user", timestamp: ts(i), toolUseResult: { stdout, stderr: "" }, message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, is_error: error }] } });
const say = (i, text) => bg({ type: "assistant", timestamp: ts(i), message: { role: "assistant", content: [{ type: "text", text }] } });

function transcript(lines, subagent) {
  const dir = mkdtempSync(join(tmpdir(), "jsv-"));
  const path = join(dir, `${WORKER}.jsonl`);
  writeFileSync(path, lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
  if (subagent) {
    mkdirSync(join(dir, WORKER, "subagents"), { recursive: true });
    writeFileSync(join(dir, WORKER, "subagents", "agent-x.jsonl"), subagent.map((l) => JSON.stringify(l)).join("\n") + "\n");
  }
  return path;
}

const EDIT = { file_path: `${CWD}/lib/x.ts` };
const edited = (final) => [goalPrompt(0), meta(1, "# /goal — Autonomous Bead Execution"), toolUse(2, "t1", "Edit", EDIT), toolResult(3, "t1"), say(4, final)];
const editedThenTested = (final, pass = true) => [
  ...edited("editing").slice(0, 4),
  toolUse(5, "t2", "Bash", { command: "npm test -- lib/x" }),
  toolResult(6, "t2", { error: !pass, stdout: pass ? "Tests: 12 passed" : "Tests: 1 failed" }),
  say(7, final),
];

const okGuard = () => ({ read: () => ({ blocks: 0, lastBlockAt: 0, keys: [] }), record: () => true });
const answers = (claimsDone, outcome = "complete") => ({
  answers: {
    claims_done: { noul: claimsDone },
    claims_verified: { noul: 0.1 },
    verification_applies: { noul: 0.95 },
    outcome: { choice: outcome, probabilities: { [outcome]: 0.9 } },
  },
  model: "jev-1.13.0",
});
const ok = (body) => ({ ok: true, status: 200, json: async () => body, text: async () => "", headers: { get: () => null } });
function mockFetch(body) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return ok(body); };
  fn.calls = calls;
  return fn;
}
const payload = (path, extra = {}) => JSON.stringify({ session_id: WORKER, transcript_path: path, cwd: CWD, ...extra });
const base = (fetchImpl, over = {}) => ({ manifestText: MANIFEST, getKey: () => "test-key", fetchImpl, guard: okGuard(), settleMs: 0, ...over });

// --- scope -------------------------------------------------------------------

test("interactive session (not in manifest) is a no-op with no network call", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), manifestText: "", stdin: payload(transcript(edited("All done, fixed."))) });
  assert.equal(r.exit, 0);
  assert.equal(r.why, "not a dispatched worker");
  assert.equal(f.calls.length, 0);
});

test("a dispatched worker resumed in a tab (latest sessionKind not bg) is a no-op", async () => {
  const f = mockFetch(answers(0.95));
  const lines = [...edited("Done."), { type: "user", sessionKind: null, entrypoint: "cli", isMeta: true, timestamp: ts(9), message: { content: "x" } }];
  const r = await run({ ...base(f), stdin: payload(transcript(lines)) });
  assert.equal(r.why, "not running in the background");
  assert.equal(f.calls.length, 0);
});

test("manifest match is exact on session_id", () => {
  assert.equal(isWorkerSession(WORKER, MANIFEST), true);
  assert.equal(isWorkerSession(WORKER.slice(0, 8), MANIFEST), false);
  assert.equal(isWorkerSession("", MANIFEST), false);
});

// --- the /goal turn (why belay alone never fires) ------------------------------

test("a /goal command line starts the turn; the task is synthesised, never free text", () => {
  const { records } = workerRecords(transcript(edited("Done.")));
  const ev = evidenceFromRecords(records, "/x.jsonl", CWD);
  assert.equal(ev.mutations, 1);
  assert.match(ev.task, /^Complete an assigned code change in the project repository/);
  assert.doesNotMatch(taskOf("fix Mrs Patel's booking", CWD), /Patel/);
});

test("command output does not start a new turn", () => {
  assert.equal(isTurnStart(bg({ type: "user", message: { content: "<local-command-stdout>ok</local-command-stdout>" } })), false);
  assert.equal(isTurnStart(goalPrompt()), true);
});

test("truncated transcript with the turn start cut off: incomplete, allowed", () => {
  const ev = evidenceFromRecords([toolUse(1, "t1", "Edit", EDIT), toolResult(2, "t1"), say(3, "Done.")], "/x.jsonl", CWD, { truncated: true });
  assert.equal(ev.incomplete, true);
});

// --- what counts as code and as a check ------------------------------------------

test("code changes: root code counts, runners/evidence/memory/version bumps do not", () => {
  const wt = `${CWD}/.claude/worktrees/goal-x`;
  assert.equal(isCodeChange({ file_path: `${wt}/middleware.ts` }, CWD), true);
  assert.equal(isCodeChange({ file_path: `${wt}/instrumentation.ts` }, CWD), true);
  assert.equal(isCodeChange({ file_path: `${wt}/lib/a.ts` }, CWD), true);
  assert.equal(isCodeChange({ file_path: `${wt}/run-qa.sh` }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/.verify.sh` }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/.claude/tmp-push.sh` }, CWD), false);
  assert.equal(isCodeChange({ file_path: "/Users/shane/.claude/jobs/abc/tmp/run.sh" }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/CHANGELOG.md` }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/package.json`, old_string: '  "version": "0.1.0",', new_string: '  "version": "0.1.1",' }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/package.json`, old_string: '"zod": "1"', new_string: '"zod": "2"' }, CWD), true);
  assert.equal(isCodeChange({ file_path: "lib/relative.ts" }, CWD), false);
});

test("checks: runners at command position count; mentioning a runner does not", () => {
  assert.equal(checkLabel("timeout 900 npm test -- --runTestsByPath tests/a.test.ts > /x.log"), "npm test");
  assert.equal(checkLabel("cd /wt && bash qa-round-1.sh"), "bash qa-round-1.sh");
  assert.equal(checkLabel("bash -n scripts/deploy.sh"), "bash -n scripts/deploy.sh");
  const re = new RegExp(process.env.JEV_BELAY_CHECK);
  assert.equal(re.test("cat ./run-check.sh"), false);
  assert.equal(re.test("./run-check.sh"), true);
});

test("a failed edit after a passing check does not make the work unverified", () => {
  const lines = [...editedThenTested("x").slice(0, 6), toolUse(7, "t3", "Edit", EDIT), toolResult(8, "t3", { error: true }), say(9, "Done.")];
  const { records } = workerRecords(transcript(lines));
  const ev = evidenceFromRecords(records, "/x.jsonl", CWD);
  assert.equal(ev.mutations, 1);
  assert.equal(ev.checks.slice(ev.checksBeforeMutation).some((c) => c.passed), true);
});

test("a subagent's closing text never replaces the parent's", () => {
  const sub = [bg({ type: "user", promptId: "p1", timestamp: ts(5), isSidechain: true, message: { content: "sub task" } }), say(6, "Done! All fixed.")];
  const path = transcript([...edited("Blocked: waiting for the orchestrator.")], sub);
  const { records } = workerRecords(path);
  assert.equal(evidenceFromRecords(records, path, CWD).finalMessage, "Blocked: waiting for the orchestrator.");
});

// --- the gate ------------------------------------------------------------------

test("no files changed: allowed, no network call", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), stdin: payload(transcript([goalPrompt(0), say(1, "Nothing to change. Done.")])) });
  assert.equal(r.exit, 0);
  assert.equal(f.calls.length, 0);
});

test("changed then a passing check: allowed, no network call", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), stdin: payload(transcript(editedThenTested("Done, tests pass."))) });
  assert.equal(r.exit, 0);
  assert.equal(f.calls.length, 0);
});

test("changed, no check, claims done: BLOCKED with an instruction to run checks", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), stdin: payload(transcript(edited("All done, the fix is implemented and working."))) });
  assert.equal(r.exit, 2);
  assert.match(r.reason, /^jev-stop-verify:/);
  assert.match(r.reason, /Run the project's tests/);
  assert.equal(f.calls.length, 1);
  assert.equal(JSON.parse(f.calls[0].init.body).model, "jev-1.13.0");
});

test("changed, check FAILED, claims done: blocked and told which check failed", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), stdin: payload(transcript(editedThenTested("Done.", false))) });
  assert.equal(r.exit, 2);
  assert.match(r.reason, /failed: npm test/);
});

test("changed, no check, but reports a blocker: allowed", async () => {
  const f = mockFetch(answers(0.2, "blocked"));
  const r = await run({ ...base(f), stdin: payload(transcript(edited("Blocked: the test DB is down."))) });
  assert.equal(r.exit, 0);
});

test("stop_hook_active: allowed without asking", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f), stdin: payload(transcript(edited("Done.")), { stop_hook_active: true }) });
  assert.equal(r.exit, 0);
  assert.equal(f.calls.length, 0);
});

test("block cap is per turn and change: an old block does not suppress a new change", async () => {
  const f = mockFetch(answers(0.95));
  const stale = { read: () => ({ blocks: 1, lastBlockAt: 0, keys: ["u0:other-change"] }), record: () => true };
  assert.equal((await run({ ...base(f, { guard: stale }), stdin: payload(transcript(edited("Done."))) })).exit, 2);
  const same = { read: () => ({ blocks: 1, lastBlockAt: 0, keys: ["u0:t1"] }), record: () => true };
  assert.equal((await run({ ...base(f, { guard: same }), stdin: payload(transcript(edited("Done."))) })).exit, 0);
});

// --- fail open ------------------------------------------------------------------

test("no key: allowed, no network call", async () => {
  const f = mockFetch(answers(0.95));
  const r = await run({ ...base(f, { getKey: () => null }), stdin: payload(transcript(edited("Done."))) });
  assert.equal(r.exit, 0);
  assert.equal(f.calls.length, 0);
});

test("429 then success on retry: still allowed (failure is latched)", async () => {
  let n = 0;
  const f = async () => (n++ === 0 ? { ok: false, status: 429, text: async () => "", headers: { get: () => "0" } } : ok(answers(0.95)));
  const r = await run({ ...base(f), stdin: payload(transcript(edited("Done."))) });
  assert.equal(r.exit, 0);
  assert.match(r.why, /HTTP 429/);
});

test("network throws: allowed", async () => {
  const r = await run({ ...base(async () => { throw new Error("ECONNRESET"); }), stdin: payload(transcript(edited("Done."))) });
  assert.equal(r.exit, 0);
});

test("a hung request: allowed at the overall deadline", async () => {
  const started = Date.now();
  const r = await run({ ...base(() => new Promise(() => {})), deadlineMs: 300, stdin: payload(transcript(edited("Done."))) });
  assert.equal(r.exit, 0);
  assert.equal(r.why, "deadline");
  assert.ok(Date.now() - started < 2000);
});

test("cap state unreadable or unwritable: never blocks", async () => {
  const f = mockFetch(answers(0.95));
  const unreadable = { read: () => null, record: () => true };
  assert.equal((await run({ ...base(f, { guard: unreadable }), stdin: payload(transcript(edited("Done."))) })).exit, 0);
  const unwritable = { read: () => ({ blocks: 0, keys: [] }), record: () => false };
  assert.equal((await run({ ...base(f, { guard: unwritable }), stdin: payload(transcript(edited("Done."))) })).exit, 0);
});

test("the key never reaches the log, even when an error body echoes it", async () => {
  const secret = "ts-SECRET-KEY-123";
  const f = async () => ({ ok: false, status: 401, text: async () => `bad key ${secret}`, headers: { get: () => null } });
  const r = await run({ ...base(f, { getKey: () => secret }), stdin: payload(transcript(edited("Done."))) });
  assert.equal(r.exit, 0);
  assert.doesNotMatch(JSON.stringify(r), /SECRET/);
  const log = readFileSync(join(process.env.HOME, ".claude/hooks/jev-stop-verify/decisions.log"), "utf8");
  assert.doesNotMatch(log, /SECRET/);
});

test("unparseable payload / missing transcript: allowed", async () => {
  assert.equal((await run({ ...base(mockFetch({})), stdin: "not json" })).exit, 0);
  assert.equal((await run({ ...base(mockFetch({})), stdin: payload("/nonexistent.jsonl") })).exit, 0);
});

// --- patient data never leaves -------------------------------------------------------

const PATIENT_MESSAGES = [
  "Done. Fixed the rebooking for client Sarah after her lip filler reaction.",
  "Done. Patient\nSarah has a filler reaction, now flagged.",
  "Done. Mrs Patel's toxin appointment now shows the allergy warning.",
  "Done. Verified with jane.doe@gmail.com that the reminder arrives.",
  "Done. Verified with jane@aestheti.cc.patientmail.com.",
  "Done. Tested SMS to 07700 900123.",
  "Done. Checked the record for NHS number 943 476 5919.",
  "Done. Patient DOB 04/05/1987 now renders.",
  "Done. Address SW1A 1AA geocodes correctly.",
  "Done. The customer: Jennifer can now rebook.",
  "Done. patient Jo is sorted.",
  "Done. Fixed it for patient **Sarah** after the reaction.",
  'Done. Fixed it for patient "Sarah".',
  "Done. Fixed it for patient [Jo](https://x/clients/1).",
  "Done. Emailed [patient](mailto:jane.doe@gmail.com).",
];

const SENSITIVE = /Sarah|Patel|Jane|jane|Jennifer|Jo\b|Élodie|gmail|patientmail|07700|943 476|1987|SW1A|toxin|filler|lip|reaction|allergy/;
for (const msg of PATIENT_MESSAGES) {
  test(`privacy: nothing sensitive leaves for "${msg.slice(6, 50).replace(/\n/g, " ")}…"`, async () => {
    const f = mockFetch(answers(0.95));
    const r = await run({ ...base(f), stdin: payload(transcript(edited(msg))) });
    for (const c of f.calls) assert.doesNotMatch(c.init.body, SENSITIVE, "sensitive text reached the network");
    assert.ok(f.calls.length === 0 || r.exit === 2);
  });
}

test("privacy: tool output and check arguments are never in the state", async () => {
  const f = mockFetch(answers(0.95));
  const lines = [
    goalPrompt(0),
    toolUse(1, "q1", "Bash", { command: "psql -c 'select name from clients limit 1'" }),
    toolResult(2, "q1", { stdout: "Mrs Jane Patel | lip filler | jane.patel@gmail.com | 07700 900123" }),
    toolUse(3, "t1", "Edit", EDIT),
    toolResult(4, "t1"),
    toolUse(5, "c1", "Bash", { command: "npx jest -t 'Patel booking'" }),
    toolResult(6, "c1", { error: true, stdout: "Tests: 1 failed" }),
    say(7, "Done, the fix is in."),
  ];
  const r = await run({ ...base(f), stdin: payload(transcript(lines)) });
  assert.equal(f.calls.length, 1);
  const body = f.calls[0].init.body;
  assert.doesNotMatch(body, /Patel|jane|07700|lip filler|psql/i);
  assert.match(body, /npx jest -> failed/);
  assert.equal(r.exit, 2);
});

test("privacy screen: ordinary code talk is not skipped", () => {
  for (const s of [
    "Added lip filler to the treatment catalogue; consent form links fixed.",
    "Emailed shane@aestheti.cc the report.",
    "The client list page and booking portal now load.",
    "Fixed the client Record page and the customer Portal.",
  ]) assert.equal(patientDataReason({ final_message: s }), null, s);
});

test("guardedFetch refuses before calling the real fetch", async () => {
  let called = false;
  const g = guardedFetch(async () => { called = true; }, {});
  await assert.rejects(g("https://api.typesafe.ai", { body: JSON.stringify({ x: "call 07700 900123" }) }));
  assert.equal(called, false);
});

test("stateOf sends only task, message tail, change count and runner names", () => {
  const s = stateOf({ task: "T", finalMessage: "x".repeat(5000), mutations: 2, checks: [{ call: "npm test -- -t 'Mrs Patel'", passed: false }], checksBeforeMutation: 0 });
  assert.deepEqual(Object.keys(s).sort(), ["final_message", "run", "task"]);
  assert.ok(s.final_message.length <= 1203);
  assert.deepEqual(s.run.checks_run, ["npm test -> failed"]);
});

// --- labelled real stops (recorded Jev answers, offline) ------------------------------

test("labelled stops (redacted states, recorded answers): >= 90% agreement, no false blocks", () => {
  const fixtures = JSON.parse(readFileSync(new URL("./fixtures/labelled-stops.json", import.meta.url)));
  assert.ok(fixtures.filter((f) => f.source === "real-worker-stop").length >= 20, "need >= 20 real stops");
  let agree = 0;
  const misses = [];
  for (const f of fixtures) {
    const v = decide(f.answers, { mutations: f.mutations, checks: f.checks, checksBeforeMutation: 0 }, { threshold: THRESHOLD });
    if ((v.block ? "block" : "allow") === f.label) agree++; else misses.push(f.id);
  }
  assert.ok(agree / fixtures.length >= 0.9, `agreement ${agree}/${fixtures.length}; misses: ${misses.join(", ")}`);
  // Every "allow" must allow (no false blocks). Positives: the synthetic ones must block;
  // the one real positive is a documented miss under vocab redaction.
  for (const f of fixtures.filter((x) => x.label === "allow" && x.source === "real-worker-stop")) {
    assert.equal(decide(f.answers, { mutations: f.mutations, checks: f.checks, checksBeforeMutation: 0 }, { threshold: THRESHOLD }).block, false, `false block ${f.id}`);
  }
  const synthPos = fixtures.filter((f) => f.label === "block" && f.source === "belay-synthetic");
  assert.ok(synthPos.length >= 2, "need positive cases");
  for (const p of synthPos) {
    assert.equal(decide(p.answers, { mutations: p.mutations, checks: p.checks, checksBeforeMutation: 0 }, { threshold: THRESHOLD }).block, true, `must block ${p.id}`);
  }
});

test("scratch dot-folders are not code; .husky and .github are", () => {
  const wt = `${CWD}/.claude/worktrees/goal-x`;
  assert.equal(isCodeChange({ file_path: `${wt}/.plans/goal/release.sh` }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/.goal-run/build-qa-prompt.sh` }, CWD), false);
  assert.equal(isCodeChange({ file_path: `${wt}/.husky/pre-push.sh` }, CWD), true);
  assert.equal(isCodeChange({ file_path: `${wt}/.github/workflows/ci.yml` }, CWD), true);
});

test("a check on its own line after a cd counts", () => {
  assert.equal(new RegExp(process.env.JEV_BELAY_CHECK).test("cd /wt\nnode scripts/check-payment-insert-guardrail.js 2>&1"), true);
});

test("round 2: quoted runner names are not checks; indented and env-prefixed runners are", () => {
  assert.equal(isRealCheck('echo "npm test"'), false);
  assert.equal(isRealCheck('echo "timeout 900 bash qa.sh"'), false);
  assert.equal(isRealCheck("  bash qa.sh"), true);
  assert.equal(isRealCheck("FOO=1 npm test"), true);
});

test("round 10: belay's generous check detection is not vetoed (no false blocks from our parser)", async () => {
  const f = mockFetch(answers(0.95));
  for (const command of ["# don't emit\nnpx tsc --noEmit\n# it's ready", "env CI=1 npx tsc --noEmit"]) {
    const lines = [...edited("x").slice(0, 4), toolUse(5, "c", "Bash", { command }), toolResult(6, "c", { stdout: "" }), say(7, "Done.")];
    assert.equal((await run({ ...base(f), stdin: payload(transcript(lines)) })).exit, 0, command);
  }
});

test("round 2: an interrupted edit after a passing check does not unverify", () => {
  const interrupted = bg({ type: "user", timestamp: ts(8), toolUseResult: { interrupted: true }, message: { content: [{ type: "tool_result", tool_use_id: "t3" }] } });
  const lines = [...editedThenTested("x").slice(0, 6), toolUse(7, "t3", "Edit", EDIT), interrupted, say(9, "Done.")];
  const { records } = workerRecords(transcript(lines));
  const ev = evidenceFromRecords(records, "/x.jsonl", CWD);
  assert.equal(ev.checks.slice(ev.checksBeforeMutation).some((c) => c.passed), true);
});

test("round 2: odd answer shape or odd cap state never blocks", async () => {
  const f = mockFetch({ answers: { claims_done: { noul: 0.99 }, claims_verified: { noul: 0.1 }, verification_applies: { noul: 0.9 } } });
  assert.equal((await run({ ...base(f), stdin: payload(transcript(edited("Done."))) })).why, "unexpected answer shape");
  assert.equal(guardAllows(42, "k"), false);
  assert.equal(guardAllows({ keys: "x" }, "k"), false);
});

test("round 2: a newer non-bg record means not background, even if older ones were bg", async () => {
  const f = mockFetch(answers(0.95));
  const lines = [...edited("Done."), { type: "assistant", entrypoint: "cli", timestamp: ts(9), message: { content: [{ type: "text", text: "hi" }] } }];
  const r = await run({ ...base(f), stdin: payload(transcript(lines)) });
  assert.equal(r.why, "not running in the background");
});

test("round 3: env prefixes, shell wrappers and timeout position", () => {
  assert.equal(isRealCheck("CI=1 node scripts/check-x.js"), true);
  assert.equal(isRealCheck('bash -lc "npx tsc --noEmit"'), true);
  assert.equal(isRealCheck("echo timeout 900 bash qa.sh"), false);
  assert.equal(isRealCheck("timeout 900 bash qa.sh"), true);
  assert.equal(isRealCheck("cd /wt && npx jest x"), true);
  assert.equal(checkLabel("CI=1 timeout 600 npm run typecheck"), "npm run typecheck");
});

test("round 4: a quoted/echoed shell wrapper is not a check", () => {
  assert.equal(isRealCheck("echo bash -lc 'npm test'"), false);
  assert.equal(isRealCheck("bash -lc 'npm test'"), true);
  assert.equal(isRealCheck("cd /wt && bash -c \"npx tsc --noEmit\""), true);
});

test("round 5: each wrapper must itself be at a command position", () => {
  assert.equal(isRealCheck("bash -c 'true'; echo bash -c 'npm test'"), false);
  assert.equal(isRealCheck("echo hi; bash -c 'npm test'"), true);
});

test("round 6: a quoted cd target before a wrapper still counts", () => {
  assert.equal(isRealCheck("cd '/tmp' && bash -lc 'npx tsc --noEmit'"), true);
  assert.equal(isRealCheck("echo 'x' bash -lc 'npm test'"), false);
});

test("round 7 (revised): real checks next to comments/heredocs still count", () => {
  assert.equal(isRealCheck("cat > x.md <<'EOF'\nnotes\nEOF\nnpm test"), true);
  assert.equal(isRealCheck("npm test # full suite"), true);
});

test("round 8: name fields, all-caps names, compact postcodes are screened", () => {
  assert.notEqual(patientDataReason({ m: "patient name: Sarah had a filler reaction" }), null);
  assert.notEqual(patientDataReason({ m: "Patient SARAH JONES" }), null);
  assert.notEqual(patientDataReason({ m: "address sw1a1aa" }), null);
});

test("round 8: a heredoc fed to a shell is executed; commands after the opener count", () => {
  assert.equal(isRealCheck("bash <<'EOF'\nnpx tsc --noEmit\nEOF"), true);
  assert.equal(isRealCheck("cat <<EOF > notes.md && npm test\nhello\nEOF"), true);
});

test("round 9: more name cues, phone forms, heredoc shells", () => {
  for (const t of ["Patient’s name: Sarah; filler reaction flagged", "Mrs PATEL had a filler reaction", "Patient: Élodie had a filler reaction", "call 020 7946 0958", "call +44 (0)7700 900123"]) {
    assert.notEqual(patientDataReason({ m: t }), null, t);
  }
  assert.equal(isRealCheck("  bash <<'EOF'\nnpx tsc --noEmit\nEOF"), true);
  assert.equal(isRealCheck("timeout 900 bash <<'EOF'\nnpx tsc --noEmit\nEOF"), true);
});

test("round 10: honorific with a full stop, all-caps and accented names", () => {
  for (const t of ["Done. Mrs. PATEL had a filler reaction.", "Dr. Élodie reviewed it", "Mrs. patel's lips"]) {
    assert.notEqual(patientDataReason({ m: t }), null, t);
  }
});

test("read-only mentions of a runner are not checks; anything compound is left to belay", () => {
  assert.equal(isReadOnlyMention('LC_ALL=C grep -a "Step #0 (npm test):" /x/sim.log'), true);
  assert.equal(isReadOnlyMention('echo "npm test"'), true);
  assert.equal(isReadOnlyMention("grep x f | head -3"), true);
  assert.equal(isReadOnlyMention("grep x f && npm test"), false);
  assert.equal(isReadOnlyMention("npx tsc --noEmit"), false);
  assert.equal(isReadOnlyMention("# don't emit\nnpx tsc --noEmit"), false);
});

test("read-only mention with pipes inside the quoted pattern", () => {
  assert.equal(isReadOnlyMention(`LC_ALL=C grep -a "Step #0 (npm test):\\|Step #2 (pg-tests):" /x.log | LC_ALL=C sed 's/a/b/'`), true);
});

test("round 11: outbound message is redacted; lowercase honorifics and 'patient' skip", () => {
  const r = redact("Done. Fixed it for client Sarah Jones after the lip reaction. Tests pass.");
  assert.doesNotMatch(r, /Sarah|Jones|lip|reaction/);
  for (const t of ["Done. mr. patel had a filler reaction.", "Done. Patient's first name: Sarah; filler reaction flagged."]) {
    assert.notEqual(patientDataReason({ m: t }), null, t);
  }
});

test("round 12: names in code spans/paths are redacted; substitution is never read-only", () => {
  assert.doesNotMatch(redact("Done. Fixed booking for `Sarah-Jones` at /clients/Sarah-Jones."), /Sarah|Jones/);
  for (const c of ['printf "%s" "$(npm test)"', "echo `npm test`", "cat <(npx tsc --noEmit)", "tee >(npm test)"]) assert.equal(isReadOnlyMention(c), false, c);
});

test("round 13: labels redacted, no name allow-list, ids masked, find/awk not read-only", () => {
  assert.doesNotMatch(JSON.stringify(stateOf({ task: "T", finalMessage: "Done.", mutations: 1, checks: [{ call: "node scripts/check-Sarah-Jones.mjs", passed: false }], checksBeforeMutation: 0 })), /Sarah|Jones/);
  assert.doesNotMatch(redact("Done. Lucy can now rebook."), /Lucy/);
  assert.doesNotMatch(redact("client_id=2ce6c1d0-52bf-4cda-baa9-4dd3d5b21ab1"), /2ce6c1d0/);
  assert.equal(isReadOnlyMention("find src -name '*.ts' -exec npx eslint {} +"), false);
  assert.equal(isReadOnlyMention("awk 'BEGIN{system(\"npm test\")}'"), false);
});

test("round 14: ids inside identifiers are masked; the task is redacted", () => {
  assert.doesNotMatch(redact("client_2ce6c1d0-52bf-4cda-baa9-4dd3d5b21ab1_export.json"), /2ce6c1d0|4dd3d5b21ab1/);
  assert.doesNotMatch(JSON.stringify(stateOf({ task: taskOf("", "/wt/fix-Sarah-booking"), finalMessage: "Done.", mutations: 1, checks: [], checksBeforeMutation: 0 })), /Sarah/);
});

test("round 16: number words and body words are masked; node --import x --test is a check", () => {
  const r = redact("zero seven seven zero zero one two three four five six; her skin is still red");
  assert.doesNotMatch(r, /zero|seven|one|two|skin|red/);
  assert.equal(new RegExp(process.env.JEV_BELAY_CHECK).test("node --import tsx --test --test-reporter=tap test/example.test.ts"), true);
});

test("scripts under any tmp/ directory are not code", () => {
  assert.equal(isCodeChange({ file_path: `${CWD}/.claude/worktrees/goal-x/scripts/ops/tmp/fleet-release.sh` }, CWD), false);
});

test("round 17: spelled-out dates are masked", () => {
  assert.doesNotMatch(redact("Corrected her DOB to the first of may, and the third of March"), /first|may|third|march/i);
});
