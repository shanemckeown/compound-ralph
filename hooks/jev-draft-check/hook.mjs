#!/usr/bin/env node
// jev-draft-check: PreToolUse check on client (clinic-owner) drafts (LUCY-4e6od).
//
// Before a Gmail draft/reply/send or a `Today's replies/... - Email|WhatsApp.md` file is
// written, it checks the copy against three of Shane's standing rules:
//   blame      no blame or apology ("that one's on us", "sorry for the delay")
//              [feedback_never_attribute_blame_in_client_copy]
//   buildState never say something isn't built ("coming soon", "not yet", "to be transparent")
//              [feedback_promises_are_build_commitments_never_retract]
//   callOffer  no unprompted call offers ("happy to jump on a quick call")
//              [feedback_no_call_offers_in_client_replies]
// Two layers: a local phrase list (always, no network) and Jev on the paraphrases the list
// misses. On a hit the write is denied once with the reason; writing the identical text
// again goes through (the session looked and decided it is right, e.g. the client asked
// for a call).
//
// Patient data (Shane, 26 Sep; orchestrator, 26 Sep): a draft that may carry patient data
// (hard identifiers, honorifics, the word "patient", or any clinical term) is NEVER sent to
// Jev; the phrase list still runs. Anything that is sent passes the allow-list redaction in
// ../jev-common/privacy.mjs, so a bare name cannot leave either.
// Fails open on every error. Decision of record: Aestheticc/Intel/JEV_DECISION_2026-09-26.md

import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync as readFile, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { guardedFetch, keychainKey, loadVocab, makeRedact, patientDataReason } from "../jev-common/privacy.mjs";

export const MODEL = "jev-1.13.0";
export const THRESHOLD = 0.8;
const HERE = join(homedir(), ".claude", "hooks", "jev-draft-check");
const LOG = join(HERE, "decisions.log");
const DEADLINE_MS = 8_000;
export const redact = makeRedact(loadVocab(new URL("./vocab.json", import.meta.url)));

// ---------------------------------------------------------------------------
// Which writes are client copy

const GMAIL = new Set(["mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__update_draft", "mcp__claude_ai_Gmail__reply", "mcp__claude_ai_Gmail__send_message"]);
const REPLY_FILE = /\/Today's replies\/(?![^/]*REPORT)[^/]*- (?:Email|WhatsApp)\.md$/i;

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", rsquo: "'", lsquo: "'", rdquo: '"', ldquo: '"' };
const decode = (t) => t.replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&#x([\da-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
  .replace(/&([a-z]+);/gi, (m, n) => ENTITIES[n.toLowerCase()] ?? m);
// Block-level tags become line breaks so words on either side never merge.
const BLOCK = /<\/?(?:br|p|div|li|ul|ol|tr|td|th|table|h[1-6]|blockquote|section|article|header|footer)\b[^>]*>/gi;
// Any other tag becomes a space (whitespace is collapsed before matching), so no tag can
// ever glue two words together.
const stripHtml = (h) => decode(String(h || "").replace(BLOCK, "\n").replace(/<[^>]+>/g, " "));

const INTERNAL = /@aestheti\.cc$/i;
const recipients = (input) => [...(input.to || []), ...(input.cc || []), ...(input.bcc || [])]
  .map((r) => (/<([^>]+)>/.exec(String(r))?.[1] ?? String(r)).trim());

/** The copy about to be written, or null if this is not client copy. */
export function draftText(toolName, input = {}) {
  if (GMAIL.has(toolName)) {
    const to = recipients(input);
    if (to.length && to.every((r) => INTERNAL.test(r.trim()))) return null; // internal mail
    return String(input.body || stripHtml(input.htmlBody) || "") || null;
  }
  const path = String(input.file_path || "");
  if (!REPLY_FILE.test(path)) return null;
  if (toolName === "Write") return String(input.content || "") || null;
  if (toolName === "Edit" || toolName === "MultiEdit") {
    // Judge the document as it will be after the edit, not the fragment.
    const edits = toolName === "Edit" ? [input] : (input.edits || []);
    let doc;
    try { doc = readFile(path, "utf8"); } catch { doc = null; }
    if (doc == null) return edits.map((e) => e?.new_string || "").join("\n") || null;
    for (const e of edits) {
      const from = String(e?.old_string ?? ""), to = String(e?.new_string ?? "");
      if (!from) continue;
      doc = e?.replace_all ? doc.split(from).join(to) : doc.replace(from, () => to);
    }
    return doc || null;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Layer 1: phrase list (local, always)

export const PHRASES = {
  blame: /\b(?:(?:that(?:'s| was| one's)|it's|this is|(?:was|is) (?:all )?) on us|(?:was|is|'s) on our (?:side|end)|our (?:fault|mistake|bug|error|bad)|(?:i'm|we're|i am|we are) (?:so |really |very )?sorry (?!to hear)|sorry (?:for|about|that)(?! to hear)|(?<!(?:don't|do not|no need to|never) )apologi[sz]e|apologies|(?:for|about) the (?:slow |late )?(?:delay|reply|response)|took (?:us )?longer than|should have (?:caught|spotted|fixed|done|come back)|owe you an apology|(?:nearly|almost|over|about|more than) (?:a|an|one|two|three|four|six|\d+) (?:days?|weeks?|months?) late|(?:message|this|it) slipped|not good enough|dropped the ball)\b/i,
  buildState: /\b(?:not (?:yet )?(?:built|available|live|ready)|isn't (?:built|live|available|ready)|hasn't been built|coming soon|on (?:our|the) roadmap|in (?:active )?development|currently (?:can't|cannot|doesn't|does not|isn't|unable)|(?:to be|being) (?:transparent|honest)|we(?:'re| are) (?:still )?(?:building|working on) (?:it|this|that)|not something (?:we|it) (?:can|does) (?:do )?yet|(?:days|weeks|months) (?:behind|away)|(?<!nothing says ")not built)\b/i,
  callOffer: /\b(?:(?:jump|hop|get) on a (?:quick )?(?:call|zoom|chat)|(?:quick|short) (?:call|zoom|chat)|happy to (?:do this|go through (?:it|this)|walk you through (?:it|this)?|chat|talk) (?:with you )?(?:on|over) (?:a |the )?(?:call|phone|zoom)|give (?:me|us) a (?:ring|call)|book (?:in )?a (?:call|zoom)|(?:call|zoom) (?:you|me) (?:if|to|when)|(?:find|set up|arrange|book) a time to (?:talk|chat|speak|go through))\b/i,
};

export function phraseHits(text) {
  const t = decode(String(text || "")).replace(/<[^>]+>/g, " ").replace(/[‘’ʼ′]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, " ");
  return Object.entries(PHRASES).filter(([, re]) => re.test(t)).map(([rule]) => rule);
}

// ---------------------------------------------------------------------------
// Layer 2: Jev on the paraphrases

export const QUESTIONS = {
  blame: { type: "noul", instructions: "Does `draft` blame, apologise for, or assign fault to the sender's business for a problem or a delay?", criteria: {
    true: "Yes: it apologises, says a problem was the sender's fault or mistake, or draws attention to lateness.",
    false: "No: it states facts and next steps without apologising or assigning fault." } },
  buildState: { type: "noul", instructions: "Does `draft` tell the reader that a feature does not exist yet, is still being built, is coming later, or cannot currently be done?", criteria: {
    true: "Yes: it reveals that something is unbuilt, in progress, planned, or not possible yet.",
    false: "No: it describes what the product does, or next steps, without saying anything is unbuilt." } },
  callOffer: { type: "noul", instructions: "Does `draft` offer or suggest a phone or video call instead of answering in writing?", criteria: {
    true: "Yes: it offers a call, meeting, zoom or phone chat.",
    false: "No: it answers in writing and offers no call." } },
};

/** Any sign of patient data means the draft never goes to Jev. */
const CLINICAL = /\b(?:botox|toxins?|fillers?|dermal|lips?|units? of|bruis\w*|swell\w*|reactions?|necrosis|occlusion|hyaluronidase|anaphyla\w*|contraindicat\w*|pregnan\w*|allerg\w*|medical history|prescri\w*|adverse|complications?|injections?|injected|numb\w*|cheeks?|doses?|aftercare|consultations?|treatment notes?|colds?|flu|fever|tablets?|pills?|medications?|medicines?|antibiotics?|symptoms?|diagnos\w*|condition|surgery|hospital|gp)\b/i;
export function patientRisk(text) {
  const hard = patientDataReason({ draft: text });
  if (hard) return hard;
  if (CLINICAL.test(text)) return "clinical detail";
  return null;
}

export function stateOf(text) {
  const t = String(text).trim();
  return { draft: redact(t.length > 2000 ? t.slice(0, 2000) : t) };
}

// ---------------------------------------------------------------------------
// Once-per-text: an identical retry after a deny goes through.

const hashOf = (t) => createHash("sha256").update(String(t)).digest("hex").slice(0, 32);

// One file per hash: no read-modify-write, so concurrent denies cannot lose each other.
const SEEN_DIR = join(HERE, "seen");
export function hasSeen(h) { return existsSync(join(SEEN_DIR, h)); }
export function recordSeen(h) {
  try { mkdirSync(SEEN_DIR, { recursive: true }); writeFileSync(join(SEEN_DIR, h), ""); return true; } catch { return false; }
}

// ---------------------------------------------------------------------------
// The hook

const REASONS = {
  blame: "blames or apologises (Shane: state the fact, then the fix; no 'on us', 'sorry', 'delay')",
  buildState: "says something isn't built yet (Shane: never tell a client something isn't built; it's a dated build task, not a confession)",
  callOffer: "offers a call (Shane: answer fully in writing; calls cost his time. Fine only if the client asked for one)",
};

function log(record, key) {
  try {
    mkdirSync(HERE, { recursive: true });
    let line = JSON.stringify({ ts: new Date().toISOString(), ...record });
    if (key) line = line.split(key).join("[key]");
    appendFileSync(LOG, `${line}\n`);
  } catch { /* never breaks the hook */ }
}

async function decideDraft({ payload, getKey, fetchImpl, seen, record }) {
  const text = draftText(payload?.tool_name, payload?.tool_input);
  if (!text) return { allow: true, why: "not client copy", quiet: true };
  const h = hashOf(text);
  if (seen(h)) return { allow: true, why: "identical retry after a flag" };

  const hits = new Set(phraseHits(text));
  const risk = patientRisk(text);
  let jev = "skipped";
  let apiKey = null;
  if (risk) {
    jev = `skipped: ${risk}`;
  } else if ((apiKey = getKey())) {
    const status = {};
    try {
      const res = await guardedFetch(fetchImpl, status)("https://api.typesafe.ai/v1/systemone", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ model: MODEL, state: stateOf(text), questions: QUESTIONS }),
      });
      const a = res.ok ? (await res.json())?.answers : null;
      if (!status.failed && a) {
        for (const rule of Object.keys(QUESTIONS)) {
          const p = a[rule]?.noul;
          if (typeof p === "number" && p >= 0 && p <= 1 && p >= THRESHOLD) hits.add(rule);
        }
        jev = "asked";
      } else jev = `unavailable: ${status.failed || "bad response"}`;
    } catch { jev = status.skipped ? `skipped: ${status.skipped}` : `unavailable: ${status.failed || "error"}`; }
  } else jev = "no key";

  if (!hits.size) return { allow: true, why: `clean (jev ${jev})`, apiKey };
  if (!record(h)) return { allow: true, why: `flagged ${[...hits]} but could not record; allowing`, apiKey };
  const reason = `jev-draft-check: this client copy ${[...hits].map((r) => REASONS[r]).join("; and ")}. Rewrite it and try again. If you've checked and it is right as written, write the identical text again and it will go through.`;
  return { allow: false, why: `flagged ${[...hits].join(",")} (jev ${jev})`, reason, apiKey };
}

export async function run({ stdin, getKey = keychainKey, fetchImpl = fetch, seen = hasSeen, record = recordSeen, deadlineMs = DEADLINE_MS } = {}) {
  let payload;
  try { payload = JSON.parse(stdin); } catch { return { allow: true, why: "unparseable payload" }; }
  let timer;
  const deadline = new Promise((resolve) => { timer = setTimeout(() => resolve({ allow: true, why: "deadline" }), deadlineMs); });
  let result;
  try {
    result = await Promise.race([decideDraft({ payload, getKey, fetchImpl, seen, record }), deadline]);
  } catch { result = { allow: true, why: "internal error" }; } finally { clearTimeout(timer); }
  const { apiKey, quiet, ...out } = result;
  if (!quiet) log({ session: payload?.session_id, tool: payload?.tool_name, allow: out.allow, why: out.why }, apiKey);
  return out;
}

async function readStdin(timeoutMs = 3_000) {
  return new Promise((resolve) => {
    let data = "";
    const timer = setTimeout(() => resolve(""), timeoutMs);
    timer.unref?.();
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => { data += c; });
    process.stdin.on("end", () => { clearTimeout(timer); resolve(data); });
    process.stdin.on("error", () => { clearTimeout(timer); resolve(""); });
  });
}

async function main() {
  setTimeout(() => process.exit(0), 12_000);
  let r;
  try { r = await run({ stdin: await readStdin() }); } catch { process.exit(0); }
  if (!r.allow) {
    process.stdout.write(`${JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: r.reason } })}\n`);
  }
  process.exit(0);
}

const invokedAs = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || "")).href; } catch { return ""; } })();
if (invokedAs === import.meta.url) main();
