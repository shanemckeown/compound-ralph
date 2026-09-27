#!/usr/bin/env node
// jev-stop-verify: Stop hook for dispatched /goal workers (LUCY-wow7r).
//
// Refuses a worker's "done" when it changed code and no check passed since. The four
// questions and the decision are jev-belay's (vendor/belay.mjs, pinned, unmodified);
// this file owns everything around them:
//   1. Background workers only: the session is in FLEET_MANIFEST.jsonl AND its latest
//      transcript record is sessionKind "bg". A worker resumed in a tab is a tab.
//   2. Worker turns. belay starts a turn at the last typed prompt and skips slash
//      commands, but a /goal worker's only prompt is "/goal <bead>", so belay alone
//      never fires on a worker. The turn is found here.
//   3. Private by construction (Shane, 26 Sep: no patient data to TypeSafe). A fixed task,
//      and the closing message and check names through the allow-list vocabulary in
//      ../jev-common/privacy.mjs. Hard identifiers skip the call outright.
//   4. Fail open. Any error, HTTP failure (even one later retried to success), missing
//      key, unwritable state or the 20 s overall deadline lets the stop through.
// Decision of record: Aestheticc/Intel/JEV_DECISION_2026-09-26.md

import { appendFileSync, closeSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, renameSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { PrivacySkip, guardedFetch, keychainKey, loadVocab, makeRedact, patientDataReason } from "../jev-common/privacy.mjs";
import { CHECK_COMMAND, QUESTIONS, ask, decide, evidenceFromTurn, homeOf, needsDoneCheck, nudge, parseJsonl, subagentLines } from "./vendor/belay.mjs";

export const MANIFEST = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/FLEET_MANIFEST.jsonl";
export const MODEL = "jev-1.13.0";
// belay ships 0.7. 0.85 here: real and synthetic true positives score >= 0.96, while
// vocab-redacted verified reports reached 0.80 (eval/, 27 Sep). A false block costs more
// than a missed one.
export const THRESHOLD = 0.85;
const HERE = join(homedir(), ".claude", "hooks", "jev-stop-verify");
const LOG = join(HERE, "decisions.log");
const DEADLINE_MS = 20_000; // host kills Stop hooks at 25 s
const ASK_TIMEOUT_MS = 12_000;
const MESSAGE_TAIL = 1200;

// ---------------------------------------------------------------------------
// Background workers only

export function isWorkerSession(sessionId, manifestText) {
  if (!sessionId) return false;
  for (const line of manifestText.split("\n")) {
    if (!line.includes(sessionId)) continue;
    try { if (JSON.parse(line).session_id === sessionId) return true; } catch { /* torn line */ }
  }
  return false;
}

/** The latest record the host stamped (it carries `entrypoint`) must say "bg". */
export function isBackground(records) {
  for (let i = records.length - 1; i >= 0; i--) {
    if (records[i] && "entrypoint" in records[i]) return records[i].sessionKind === "bg";
  }
  return false;
}

// ---------------------------------------------------------------------------
// What counts as a code change, and as a check

const MUTATING = new Set(["Write", "Edit", "MultiEdit", "NotebookEdit"]);
const CODE_FILE = /\.(?:tsx?|jsx?|mjs|cjs|sql|sh|bash|zsh|py|rb|go|rs|swift|kt|java|css|scss|ya?ml|toml|json|prisma)$/i;
const NOT_CODE_PATH = /\/\.claude\/(?:evidence|jobs|projects\/[^/]+\/memory|plans)\/|\/\.goal\/|\/tmp\/|\/Documents\/Obsidian\/|\/\.beads\/|\/worktrees\/[^/]+\/\.(?!husky\/|github\/)[^/]+\/|(?:^|\/)_?tmp[-_][^/]*$|\/package-lock\.json$|(?:^|\/)(?:CHANGELOG|VERSION)(?:\.md)?$/;
// Workers write throwaway runners at the worktree root (run-qa.sh, .verify.sh,
// .ship2.sh, probe-anchors.mjs) and delete them. Real root code (middleware.ts,
// instrumentation.ts, next.config.mjs) is not named like that.
const WORKTREE_ROOT_FILE = /\/worktrees\/[^/]+\/([^/]+)$/;
const ROOT_RUNNER = /^(?:\..+|(?:run|qa|probe|verify|prove|ship|final|control|review|build-qa|check)[-_.].*|.*\.(?:sh|py))$/i;
const VERSION_ONLY = /^\s*"version"\s*:\s*"[^"]*",?\s*$/;

export function isCodeChange(input = {}, cwd = "") {
  const path = String(input.file_path || input.notebook_path || "");
  if (!path.startsWith("/") || !CODE_FILE.test(path) || NOT_CODE_PATH.test(path)) return false;
  if (!(cwd && path.startsWith(`${cwd.replace(/\/$/, "")}/`)) && !/\/worktrees\//.test(path)) return false;
  if (/\/package\.json$/.test(path) && VERSION_ONLY.test(input.old_string ?? "x") && VERSION_ONLY.test(input.new_string ?? "x")) return false;
  const root = WORKTREE_ROOT_FILE.exec(path);
  return !(root && ROOT_RUNNER.test(root[1]));
}

// The runners workers really use, only where a command starts: `bash .verify.sh`,
// `timeout 900 bash qa-round-1.sh`, `./run-gates-x.sh`, `bash -n x.sh`, `npm run qa`,
// `python3 gated-test-run.py`, `node scripts/check-x.js`. belay reads this once from env.
const AT_COMMAND = String.raw`(?:^\s*|[;&|(\n]\s*)(?:\w+=\S*\s+)*(?:timeout\s+\d+[smh]?\s+)?(?:\w+=\S*\s+)*`;
export const EXTRA_CHECK = String.raw`${AT_COMMAND}(?:(?:bash|sh|zsh)\s+(?:-n\s+\S+|\S*(?:qa|test|gate|check|verify|prove|lint|typecheck|ship|control|final|sim|suite)\S*\.sh\b)|\./\S*(?:qa|test|gate|check|verify|prove|lint|typecheck|ship|control|final|sim|suite)\S*\.sh\b|(?:npm|bun)\s+run\s+(?:qa|gate|test:\S+)\b|python3?\s+\S*gated-test-run\.py|node\s+\S*(?:check|verify|test|guard)\S*\.[mc]?js\b|node\s+(?:--?[\w-]+(?:[= ](?!--)\S+)?\s+)*--test\b)`;
process.env.JEV_BELAY_CHECK = EXTRA_CHECK;
const EXTRA_RE = new RegExp(EXTRA_CHECK);

// isRealCheck is a stricter reading of "a runner at a command position". It is NOT used to
// veto belay's own (generous) check detection: that veto was tried in Astra rounds 2-10
// and every version could erase a real check and cause a false block, only to fix
// missed blocks, which fail open anyway. Kept for checkLabel and tests.
const CHECK_AT = new RegExp(`${AT_COMMAND}(?:${CHECK_COMMAND.source})`);
// Quoted spans become a placeholder. (Heredoc and comment stripping was tried and
// removed: it could erase a real check and cause a false block, to fix only missed
// blocks, which fail open anyway.)
function unquoted(command) { return String(command || "").replace(/'[^']*'|"(?:\\.|[^"\\])*"/g, "QUOTED"); }
const SHELL_WRAPPED = /\b(?:bash|sh|zsh)\s+-\w*c\s+(?:'([^']*)'|"((?:\\.|[^"\\])*)")/g;
// A wrapper counts only if the text before it (quotes balanced, quoted text blanked)
// ends at a real command position.
const ENDS_AT_COMMAND = new RegExp(`${AT_COMMAND}$`);
function atCommandPosition(before) {
  const u = unquoted(before);
  return !/['"]/.test(u) && ENDS_AT_COMMAND.test(u);
}
export function isRealCheck(command, depth = 0) {
  const raw = String(command || "");
  const c = unquoted(raw);
  if (CHECK_AT.test(c) || EXTRA_RE.test(c)) return true;
  if (depth > 1) return false;
  for (const m of raw.matchAll(SHELL_WRAPPED)) {
    if (atCommandPosition(raw.slice(0, m.index)) && isRealCheck(m[1] ?? m[2], depth + 1)) return true;
  }
  return false;
}

// The one veto kept: a command made only of reading tools (grep, cat, echo, tail...) that
// merely mentions a runner, e.g. `grep "npm test" old.log`. With no ; && || or newline it
// cannot hide a real check, so this can only turn a false "verified" into a real gap.
// find (-exec) and awk (system()) can run commands, so they are not reading tools here.
const READ_ONLY = /^(?:grep|rg|egrep|cat|echo|printf|head|tail|less|sed|wc|ls|jq|sort|uniq|cut|tr)$/;
export function isReadOnlyMention(command) {
  if (/\$\(|`|[<>]\(/.test(String(command || ""))) return false; // substitution executes
  const c = unquoted(command); // separators and pipes inside quotes are not structure
  if (/[\n;]|&&|\|\|/.test(c)) return false;
  return c.split("|").every((seg) => READ_ONLY.test(seg.trim().replace(/^(?:\w+=\S*\s+)+/, "").split(/\s+/)[0] || ""));
}

/** A check as a runner name only, never its arguments. */
export function checkLabel(call) {
  const c = String(call || "");
  const m = CHECK_COMMAND.exec(c) || EXTRA_RE.exec(c);
  return (m ? m[0].replace(/^[\s;&|(]+/, "").replace(/^(?:\w+=\S*\s+)*(?:timeout\s+\d+[smh]?\s+)?(?:\w+=\S*\s+)*/, "").trim() : "check").slice(0, 60);
}

/** Drop non-code and failed writes from belay's view; remember the last code write. */
function codeMutationsOnly(lines, cwd) {
  const failed = new Set();
  for (const l of lines) {
    if (l?.type === "user" && Array.isArray(l.message?.content)) {
      const interrupted = Boolean(l.toolUseResult?.interrupted);
      for (const b of l.message.content) if (b?.type === "tool_result" && (b.is_error || interrupted)) failed.add(b.tool_use_id);
    }
  }
  let lastId = null;
  const out = lines.map((line) => {
    if (line?.type !== "assistant" || !Array.isArray(line.message?.content)) return line;
    let changed = false;
    const content = line.message.content.map((b) => {
      if (b?.type === "tool_use" && b.name === "Bash" && isReadOnlyMention(b.input?.command)) { changed = true; return { ...b, name: "BashRead" }; }
      if (b?.type !== "tool_use" || !MUTATING.has(b.name)) return b;
      if (!failed.has(b.id) && isCodeChange(b.input, cwd)) { lastId = b.id; return b; }
      changed = true;
      return { ...b, name: `Ignored${b.name}` };
    });
    return changed ? { ...line, message: { ...line.message, content } } : line;
  });
  return { lines: out, lastId };
}

// ---------------------------------------------------------------------------
// The worker turn

const TAIL_BYTES = 8 * 1024 * 1024;

function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter((b) => b?.type === "text").map((b) => b.text || "").join("\n");
}

/** A typed prompt or a slash command starts a turn; command output does not. */
export function isTurnStart(line) {
  if (line?.type !== "user" || line.toolUseResult || line.isMeta || line.isSidechain) return false;
  const text = textOf(line.message?.content).trim();
  if (!text || /^<local-command-(?:stdout|stderr)>/.test(text)) return false;
  return !/^<command-(?:message|args)>/.test(text) || /<command-name>/.test(text);
}

/** Never free text: a fixed task. (The bead id and repo name are not needed.) */
export function taskOf() {
  return "Complete an assigned code change in the project repository, verified by its tests and checks, ending at a pushed branch.";
}

function readTail(path) {
  const size = statSync(path).size;
  if (size <= TAIL_BYTES) return { text: readFileSync(path, "utf8"), truncated: false };
  const fd = openSync(path, "r");
  try {
    const buf = Buffer.alloc(TAIL_BYTES);
    readSync(fd, buf, 0, TAIL_BYTES, size - TAIL_BYTES);
    const text = buf.toString("utf8");
    return { text: text.slice(text.indexOf("\n") + 1), truncated: true };
  } finally { closeSync(fd); }
}

function lastAssistantText(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i];
    if (l?.type === "assistant" && !l.isSidechain) { const t = textOf(l.message?.content).trim(); if (t) return t; }
  }
  return "";
}

/** Evidence for the worker turn ending at the last record, in belay's shape. */
export function evidenceFromRecords(records, transcriptPath, cwd, { until, truncated = false } = {}) {
  let startAt = -1;
  for (let i = records.length - 1; i >= 0; i--) if (isTurnStart(records[i])) { startAt = i; break; }
  // The turn began before the part we read: evidence is incomplete, so allow.
  if (startAt < 0 && truncated) return { incomplete: true, mutations: 0, checks: [], checksBeforeMutation: 0, finalMessage: "", task: "" };
  const start = startAt >= 0 ? records[startAt] : null;
  const lines = records.slice(startAt + 1);
  let extra = start?.promptId ? subagentLines(transcriptPath, start.promptId) : [];
  if (until) extra = extra.filter((l) => String(l.timestamp || "") <= until);
  const merged = extra.length
    ? [...lines, ...extra].sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || "")))
    : lines;
  const { lines: code, lastId } = codeMutationsOnly(merged, cwd);
  const evidence = evidenceFromTurn(code, homeOf(transcriptPath));
  return {
    ...evidence,
    finalMessage: lastAssistantText(lines), // the parent's words, never a subagent's
    task: taskOf(start ? textOf(start.message?.content) : "", cwd),
    turnId: start?.uuid || start?.promptId || "no-start",
    lastMutationId: lastId,
  };
}

export function workerRecords(transcriptPath) {
  const { text, truncated } = readTail(transcriptPath);
  return { records: parseJsonl(text), truncated };
}

const VOCAB = loadVocab(new URL("./vocab.json", import.meta.url));
export const redact = makeRedact(VOCAB);

/** The outbound state: belay's shape, minimum content. */
export function stateOf(evidence) {
  const fresh = evidence.checks.slice(evidence.checksBeforeMutation ?? 0);
  const msg = String(evidence.finalMessage || "").trim();
  return {
    task: evidence.task,
    final_message: redact(msg.length > MESSAGE_TAIL ? `...${msg.slice(-MESSAGE_TAIL)}` : msg),
    run: {
      file_changes: evidence.mutations,
      checks_run: fresh.map((c) => `${redact(checkLabel(c.call))} -> ${c.passed ? "passed" : "failed"}`),
    },
  };
}

// ---------------------------------------------------------------------------
// Block caps: one block per turn and change, at most 3 per session, 60 s apart.
// If the cap state cannot be read or written, do not block.

const STATE_DIR = join(HERE, "sessions");
const MAX_BLOCKS = 3;
const COOLDOWN_MS = 60_000;

function sessionPath(id) { return join(STATE_DIR, `${String(id || "unknown").replace(/[^\w.-]/g, "_")}.json`); }

export function readGuard(id) {
  let raw;
  try { raw = readFileSync(sessionPath(id), "utf8"); } catch (err) {
    return err?.code === "ENOENT" ? { blocks: 0, lastBlockAt: 0, keys: [] } : null;
  }
  try { return JSON.parse(raw); } catch { return null; }
}

export function guardAllows(state, key, now = Date.now()) {
  if (!state || typeof state !== "object" || Array.isArray(state) || (state.keys !== undefined && !Array.isArray(state.keys))) return false;
  if (state.keys?.includes(key)) return false;
  if ((state.blocks || 0) >= MAX_BLOCKS) return false;
  return now - (state.lastBlockAt || 0) >= COOLDOWN_MS;
}

export function recordBlock(id, key) {
  try {
    const now = readGuard(id);
    if (!now) return false;
    mkdirSync(STATE_DIR, { recursive: true });
    const tmp = `${sessionPath(id)}.${process.pid}.tmp`;
    writeFileSync(tmp, JSON.stringify({ blocks: (now.blocks || 0) + 1, lastBlockAt: Date.now(), keys: [...(now.keys || []), key].slice(-20) }));
    renameSync(tmp, sessionPath(id));
    return true;
  } catch { return false; }
}

export { PrivacySkip, guardedFetch, keychainKey, patientDataReason };

// ---------------------------------------------------------------------------
// The hook

function log(record, key) {
  try {
    mkdirSync(HERE, { recursive: true });
    let line = JSON.stringify({ ts: new Date().toISOString(), ...record });
    if (key) line = line.split(key).join("[key]");
    appendFileSync(LOG, `${line}\n`);
  } catch { /* logging never breaks the hook */ }
}

async function decideStop({ payload, manifestText, getKey, fetchImpl, guard, settleMs }) {
  if (!isWorkerSession(payload?.session_id, manifestText)) return { exit: 0, why: "not a dispatched worker", quiet: true };
  if (payload.stop_hook_active) return { exit: 0, why: "stop hook already active" };

  const read = () => {
    const { records, truncated } = workerRecords(payload.transcript_path);
    return { bg: isBackground(records), ev: evidenceFromRecords(records, payload.transcript_path, payload.cwd, { truncated }) };
  };
  let seen;
  try { seen = read(); } catch { return { exit: 0, why: "no readable transcript" }; }
  if (!seen.bg) return { exit: 0, why: "not running in the background", quiet: true };
  let evidence = seen.ev;
  if (evidence.incomplete) return { exit: 0, why: "turn start outside the tail: evidence incomplete" };
  if (!needsDoneCheck(evidence)) return { exit: 0, why: "gate: nothing to check" };

  const said = typeof payload.last_assistant_message === "string" ? payload.last_assistant_message.trim() : "";
  if (said && !/… \[\+\d+ chars\]$/.test(said)) {
    evidence = { ...evidence, finalMessage: said };
  } else if (settleMs > 0) {
    // Stop fires ~100 ms before the closing message lands in the transcript.
    await new Promise((r) => setTimeout(r, settleMs));
    let again;
    try { again = read(); } catch { return { exit: 0, why: "reread failed" }; }
    if (!again.bg || again.ev.incomplete || again.ev.turnId !== evidence.turnId) return { exit: 0, why: "session changed while settling" };
    evidence = again.ev;
    if (!needsDoneCheck(evidence)) return { exit: 0, why: "gate: nothing to check" };
  }

  const blockKey = `${evidence.turnId}:${evidence.lastMutationId}`;
  if (!guardAllows(guard.read(payload.session_id), blockKey)) return { exit: 0, why: "guard: capped, already blocked here, or state unreadable" };

  const apiKey = getKey();
  if (!apiKey) return { exit: 0, why: "no key" };

  // Screen before asking (belay would otherwise retry a refused send), and again at the
  // network edge in guardedFetch.
  const state = stateOf(evidence);
  const pii = patientDataReason({ full: evidence.finalMessage }) || patientDataReason(state);
  if (pii) return { exit: 0, why: `privacy skip: ${pii}`, apiKey };

  const status = {};
  let answer;
  try {
    answer = await ask(state, QUESTIONS, {
      env: { JEV_API_KEY: apiKey, JEV_MODEL: MODEL },
      fetchImpl: guardedFetch(fetchImpl, status),
      timeoutMs: ASK_TIMEOUT_MS,
    });
  } catch {
    return { exit: 0, why: status.skipped ? `privacy skip: ${status.skipped}` : `jev unreachable: ${status.failed || "error"}`, apiKey };
  }
  if (status.failed) return { exit: 0, why: `jev unreliable this call: ${status.failed}`, apiKey };

  const a = answer?.answers;
  const num = (q) => typeof a?.[q]?.noul === "number" && Number.isFinite(a[q].noul);
  if (!num("claims_done") || !num("claims_verified") || !num("verification_applies") || typeof a?.outcome?.choice !== "string") {
    return { exit: 0, why: "unexpected answer shape", apiKey };
  }
  const verdict = decide(a, evidence, { threshold: THRESHOLD });
  const extra = { answers: answer.answers, model: answer.model, latency_ms: answer.elapsedMs, mutations: evidence.mutations };
  if (!verdict.block) return { exit: 0, why: "allowed", extra, apiKey };
  if (!guard.record(payload.session_id, blockKey)) return { exit: 0, why: "would block, but cap state unwritable", extra, apiKey };
  return { exit: 2, why: "blocked", reason: nudge(verdict, evidence).replace(/^jev-belay:/, "jev-stop-verify:"), extra, apiKey };
}

export async function run({
  stdin,
  manifestText = (() => { try { return readFileSync(MANIFEST, "utf8"); } catch { return ""; } })(),
  getKey = keychainKey,
  fetchImpl = fetch,
  guard = { read: readGuard, record: recordBlock },
  settleMs = 300,
  deadlineMs = DEADLINE_MS,
} = {}) {
  let payload;
  try { payload = JSON.parse(stdin); } catch { return { exit: 0, why: "unparseable payload" }; }
  let timer;
  const deadline = new Promise((resolve) => { timer = setTimeout(() => resolve({ exit: 0, why: "deadline" }), deadlineMs); });
  let result;
  try {
    result = await Promise.race([decideStop({ payload, manifestText, getKey, fetchImpl, guard, settleMs }), deadline]);
  } catch {
    result = { exit: 0, why: "internal error" };
  } finally { clearTimeout(timer); }
  const { apiKey, quiet, ...out } = result;
  if (!quiet) log({ session: payload?.session_id, exit: out.exit, why: out.why, ...(out.extra || {}) }, apiKey);
  return out;
}

async function readStdin(timeoutMs = 5_000) {
  return new Promise((resolve) => {
    let data = "";
    // No EOF in time means no trustworthy payload: allow.
    const timer = setTimeout(() => resolve(""), timeoutMs);
    timer.unref?.();
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => { data += c; });
    process.stdin.on("end", () => { clearTimeout(timer); resolve(data); });
    process.stdin.on("error", () => { clearTimeout(timer); resolve(""); });
  });
}

async function main() {
  // Last resort: whatever happens, exit 0 before the host's 25 s kill.
  setTimeout(() => process.exit(0), 22_000);
  let result;
  try {
    result = await run({ stdin: await readStdin() });
  } catch {
    process.exit(0);
  }
  if (result.exit === 2) {
    process.stderr.write(`${result.reason}\n`);
    process.exit(2);
  }
  process.exit(0);
}

const invokedAs = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || "")).href; } catch { return ""; } })();
if (invokedAs === import.meta.url) main();
