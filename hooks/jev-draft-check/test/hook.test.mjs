// Offline tests for jev-draft-check (LUCY-4e6od). No network, no key: fetch is mocked.
// Run: node --test ~/.claude/hooks/jev-draft-check/test/hook.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { THRESHOLD, draftText, patientRisk, phraseHits, redact, run, stateOf } from "../hook.mjs";

const answers = (o = {}) => ({ answers: { blame: { noul: o.blame ?? 0.05 }, buildState: { noul: o.buildState ?? 0.05 }, callOffer: { noul: o.callOffer ?? 0.05 } } });
const ok = (body) => ({ ok: true, status: 200, json: async () => body, text: async () => "", headers: { get: () => null } });
function mockFetch(body) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return ok(body); };
  fn.calls = calls;
  return fn;
}
function memSeen() { const set = new Set(); return { seen: (h) => set.has(h), record: (h) => { set.add(h); return true; } }; }
const gmail = (body) => JSON.stringify({ session_id: "s", tool_name: "mcp__claude_ai_Gmail__create_draft", tool_input: { to: ["owner@clinic.co.uk"], body } });
const base = (f, over = {}) => ({ getKey: () => "k", fetchImpl: f, ...memSeen(), ...over });

// --- which writes are client copy ------------------------------------------------

test("client copy: Gmail drafts/replies/sends and Today's replies Email/WhatsApp files only", () => {
  assert.equal(draftText("mcp__claude_ai_Gmail__create_draft", { body: "Hi" }), "Hi");
  assert.match(draftText("mcp__claude_ai_Gmail__reply", { htmlBody: "<p>Hi<br>there</p>" }), /Hi\s+there/);
  assert.equal(draftText("mcp__claude_ai_Gmail__send_message", { draftId: "d1" }), null);
  const f = "/Users/shane/Documents/Obsidian/Today's replies/2026-09-27 - Kelly - Omorphia - Email.md";
  assert.equal(draftText("Write", { file_path: f, content: "Hi Kelly" }), "Hi Kelly");
  assert.equal(draftText("Edit", { file_path: f, old_string: "a", new_string: "x" }), "x"); // file absent: fragment
  assert.equal(draftText("Write", { file_path: "/Users/shane/Documents/Obsidian/Today's replies/START HERE.md", content: "x" }), null);
  assert.equal(draftText("Write", { file_path: "/Users/shane/Documents/Obsidian/Today's replies/2026-09-18 - REPORT - owed - Email.md", content: "x" }), null);
  assert.equal(draftText("Write", { file_path: "/tmp/notes.md", content: "x" }), null);
});

test("not client copy: allowed, nothing asked, nothing logged", async () => {
  const f = mockFetch(answers());
  const r = await run({ ...base(f), stdin: JSON.stringify({ tool_name: "Write", tool_input: { file_path: "/tmp/x.md", content: "sorry" } }) });
  assert.equal(r.allow, true);
  assert.equal(f.calls.length, 0);
});

// --- phrase list (Shane's own examples) -------------------------------------------------

test("phrase list catches the wording from the feedback memories", () => {
  for (const [t, rule] of [
    ["Ah sorry both, that one's on us not you.", "blame"],
    ["Sorry that ran well past the 12 hours I said.", "blame"],
    ["Apologies for the slow reply.", "blame"],
    ["I owe you an apology, this is nearly a month late.", "blame"],
    ["That feature is coming soon.", "buildState"],
    ["To be transparent, that isn't built yet.", "buildState"],
    ["Tap to Pay is a couple of weeks behind it.", "buildState"],
    ["Happy to jump on a quick call if that's easier.", "callOffer"],
    ["which I'm happy to do with you on a quick call.", "callOffer"],
  ]) assert.ok(phraseHits(t).includes(rule), `${rule}: ${t}`);
});

test("phrase list leaves condolences and neutral wording alone", () => {
  for (const t of [
    "Tom, so sorry to hear about your friend. Please don't apologise for the spreadsheet.",
    "On our side, every clinic that takes payments goes through Stripe.",
    "The reminders now go out at 9am the day before.",
    "Nothing has been taken through the system yet.",
  ]) assert.deepEqual(phraseHits(t), [], t);
});

// --- the decision ------------------------------------------------------------------------

test("clean draft: allowed", async () => {
  const f = mockFetch(answers());
  const r = await run({ ...base(f), stdin: gmail("Hi Chris,\n\nThe reminders now go out at 9am.\n\nShane") });
  assert.equal(r.allow, true);
  assert.equal(f.calls.length, 1);
});

test("phrase hit: denied with the rule, even with no key", async () => {
  const f = mockFetch(answers());
  const r = await run({ ...base(f, { getKey: () => null }), stdin: gmail("Hi, apologies for the slow reply. It's fixed.") });
  assert.equal(r.allow, false);
  assert.match(r.reason, /blames or apologises/);
  assert.equal(f.calls.length, 0);
});

test("paraphrase only Jev sees: denied", async () => {
  const f = mockFetch(answers({ buildState: 0.96 }));
  const r = await run({ ...base(f), stdin: gmail("Hi Leah,\n\nThe stock export is something we're putting the finishing touches on.\n\nShane") });
  assert.equal(r.allow, false);
  assert.match(r.reason, /isn't built/);
});

test("below threshold: allowed", async () => {
  const f = mockFetch(answers({ buildState: THRESHOLD - 0.05 }));
  assert.equal((await run({ ...base(f), stdin: gmail("Hi Chris, nothing has been recorded against it yet.") })).allow, true);
});

test("identical retry after a flag goes through; a changed text is checked again", async () => {
  const f = mockFetch(answers());
  const mem = memSeen();
  const text = "Hi, happy to jump on a quick call.";
  assert.equal((await run({ ...base(f, mem), stdin: gmail(text) })).allow, false);
  assert.equal((await run({ ...base(f, mem), stdin: gmail(text) })).allow, true);
  assert.equal((await run({ ...base(f, mem), stdin: gmail(`${text} Thanks`) })).allow, false);
});

// --- fail open ---------------------------------------------------------------------------

test("fail open: 429, network error, bad JSON shape, hung request, unrecordable flag", async () => {
  const clean = gmail("Hi, the stock export is on its way to you.");
  const r429 = async () => ({ ok: false, status: 429, text: async () => "", headers: { get: () => null } });
  assert.equal((await run({ ...base(r429), stdin: clean })).allow, true);
  assert.equal((await run({ ...base(async () => { throw new Error("x"); }), stdin: clean })).allow, true);
  assert.equal((await run({ ...base(mockFetch({ nope: 1 })), stdin: clean })).allow, true);
  const t0 = Date.now();
  assert.equal((await run({ ...base(() => new Promise(() => {})), deadlineMs: 200, stdin: clean })).allow, true);
  assert.ok(Date.now() - t0 < 2000);
  const noRecord = { seen: () => false, record: () => false };
  assert.equal((await run({ ...base(mockFetch(answers()), noRecord), stdin: gmail("Sorry for the delay.") })).allow, true);
  assert.equal((await run({ ...base(mockFetch(answers())), stdin: "not json" })).allow, true);
});

// --- patient data (orchestrator requirement) ---------------------------------------------------

for (const [label, text] of [
  ["patient name + treatment detail", "Hi Leah, patient Sarah Jones had a filler reaction; I've moved her review to Friday."],
  ["patient name (honorific)", "Hi Leah, Mrs Patel's appointment is now on Friday at 10."],
  ["patient name ('patient' cue)", "Hi Leah, your patient Jennifer has rebooked."],
  ["treatment detail only", "Hi Leah, the 2ml lip filler top-up is now priced at £180."],
  ["patient contact", "Hi Leah, I've updated the record to jane.doe@gmail.com and 07700 900123."],
]) {
  test(`patient data never reaches Jev: ${label}`, async () => {
    const f = mockFetch(answers());
    const r = await run({ ...base(f), stdin: gmail(text) });
    assert.equal(f.calls.length, 0, "network must not be called");
    assert.equal(r.allow, true);
    assert.ok(patientRisk(text));
  });
}

test("a bare name with no cue can reach Jev, but only redacted", async () => {
  const f = mockFetch(answers());
  await run({ ...base(f), stdin: gmail("Hi Leah,\n\nSarah Jones has rebooked for Friday the 3rd at 10:30, and paid the deposit.\n\nShane") });
  assert.equal(f.calls.length, 1);
  assert.doesNotMatch(f.calls[0].init.body, /Sarah|Jones|Leah|Friday|3rd|10:30|Shane/);
});

test("redaction keeps the rule words Jev needs", () => {
  assert.match(redact("Apologies, that was an oversight and it's coming soon; happy to jump on a quick call."), /apologies|oversight|coming soon|quick call/i);
  assert.doesNotMatch(JSON.stringify(stateOf("Dear Mrs Okafor, re 14 High Street")), /Okafor|High|14/);
});

// --- labelled corpus (recorded answers, offline) ----------------------------------------------------

test("labelled drafts: >= 85% agreement (26 real replies + 8 paraphrases), no missed real flags", () => {
  const rows = JSON.parse(readFileSync(new URL("./labelled-drafts.json", import.meta.url)));
  assert.ok(rows.length >= 15);
  let agree = 0;
  const misses = [];
  for (const x of rows) {
    const hits = new Set(x.phrases);
    if (x.jev) for (const [k, v] of Object.entries(x.jev)) if (v >= THRESHOLD) hits.add(k);
    const got = hits.size ? "flag" : "clean";
    if (got === x.label) agree++; else misses.push(x.id);
  }
  assert.ok(agree / rows.length >= 0.85, `agreement ${agree}/${rows.length}; misses ${misses.join(", ")}`);
  for (const x of rows) assert.doesNotMatch(JSON.stringify(x), /"text"/, "fixtures never store raw draft text");
});

test("review r1: internal mail is not client copy; curly apostrophes; out-of-range scores; clinical words masked", async () => {
  assert.equal(draftText("mcp__claude_ai_Gmail__create_draft", { to: ["engineer@aestheti.cc"], body: "The release is in development." }), null);
  assert.notEqual(draftText("mcp__claude_ai_Gmail__create_draft", { to: ["owner@clinic.co.uk", "shane@aestheti.cc"], body: "x" }), null);
  assert.ok(phraseHits("The reminder went out twice; that one’s on us").includes("blame"));
  const f = mockFetch({ answers: { blame: { noul: 99 }, buildState: { noul: 0.1 }, callOffer: { noul: 0.1 } } });
  assert.equal((await run({ ...base(f), stdin: gmail("Hi Chris, the reminders go out at 9am.") })).allow, true);
  assert.ok(patientRisk("Sarah has a cold and is taking a tablet every morning"));
  assert.doesNotMatch(redact("she has a cold and is taking a tablet every morning"), /cold|tablet|morning/);
});

test("review r2: display-name addresses, HTML entities and tags", () => {
  assert.equal(draftText("mcp__claude_ai_Gmail__create_draft", { to: ["Shane <shane@aestheti.cc>"], body: "in development" }), null);
  assert.ok(phraseHits(draftText("mcp__claude_ai_Gmail__create_draft", { htmlBody: "<p>That one&#39;s on us</p>" })).includes("blame"));
  assert.ok(phraseHits("Happy to jump on a <strong>quick</strong> call").includes("callOffer"));
});

test("review r3: HTML block boundaries; edits judged on the resulting document", () => {
  assert.ok(phraseHits(draftText("mcp__claude_ai_Gmail__create_draft", { htmlBody: "<div>Coming soon</div><div>Shane</div>" })).includes("buildState"));
  const dir = join(mkdtempSync(join(tmpdir(), "jdc-")), "Today's replies");
  mkdirSync(dir);
  const f = join(dir, "2026-09-27 - Leah - Viso - Email.md");
  writeFileSync(f, "Hi Leah, the export is available soon.");
  const doc = draftText("Edit", { file_path: f, old_string: "available", new_string: "coming" });
  assert.equal(doc, "Hi Leah, the export is coming soon.");
  assert.ok(phraseHits(doc).includes("buildState"));
});

test("review r4: no tag can glue words together", () => {
  for (const html of ["Coming soon<hr>Shane", "Coming soon<img src=x>Shane", "Coming soon<span></span>Shane"]) {
    assert.ok(phraseHits(draftText("mcp__claude_ai_Gmail__create_draft", { htmlBody: html })).includes("buildState"), html);
  }
});

test("review r5: tags in Markdown reply files never glue words either", () => {
  assert.ok(phraseHits("Coming soon<br>Shane").includes("buildState"));
});
