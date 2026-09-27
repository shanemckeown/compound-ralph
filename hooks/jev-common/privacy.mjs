// Shared privacy layer for Jev hooks (jev-stop-verify, jev-draft-check).
// Shane, 26 Sep 2026: no patient data to TypeSafe. Two layers:
//   1. patientDataReason: hard identifiers, honorifics or the word "patient" mean skip.
//   2. makeRedact(vocab): private by construction, only allow-listed words leave.
// Reviewed with jev-stop-verify (Astra xhigh, 17 rounds). Change with care: both hooks use it.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

// ---------------------------------------------------------------------------
// Patient-data screen, on every string value of the outbound state, whitespace
// collapsed. Over-skipping is safe (nothing is sent, the stop is allowed).

const INTERNAL_EMAIL = /@(?:aestheti\.cc|example\.(?:com|org)|anthropic\.com|users\.noreply\.github\.com)$/i;
const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
// Any run of 10+ digits with phone punctuation (UK mobile/landline, +44 (0) forms).
const PHONE = /(?:\+\s?\d|\(?\b0)[\d\s().-]{8,}\d/;
const digitsIn = (m) => (m.match(/\d/g) || []).length;
const POSTCODE = /\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b/i;
const NHS_NUMBER = /\b\d{3}[\s-]?\d{3}[\s-]?\d{4}\b/;
const DOB = /\b(?:dob|date of birth|born)\b[^.]{0,20}\d/i;
const CLINICAL = /\b(?:botox|toxin|filler|dermal|lips?|units? of|bruis\w*|swelling|reaction|necrosis|occlusion|hyaluronidase|anaphyla\w*|contraindicat\w*|pregnan\w*|allerg\w*|medical history|prescri\w*|adverse|complication|treatment notes?)\b/i;
const CLINICAL_G = new RegExp(CLINICAL.source, "gi");
// A named person: an honorific, or client/patient/customer then a capitalised word.
const HONORIFIC = /\b(?:mr|mrs|ms|miss|mx|dr)\.?\s+\p{L}/iu;
const PATIENT_WORD = /\bpatients?\b/i;

function strings(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === "object") Object.values(value).forEach((v) => strings(v, out));
  return out;
}

export function patientDataReason(input) {
  let value = input;
  if (typeof input === "string") { try { value = JSON.parse(input); } catch { value = input; } }
  // Identifiers on the raw text (link targets included), names on a normalised copy.
  const raw = strings(value).join(" \n ").replace(/\s+/g, " ");
  if ((raw.match(EMAIL) || []).some((e) => !INTERNAL_EMAIL.test(e))) return "email address";
  for (const m of raw.matchAll(new RegExp(PHONE.source, "g"))) if (digitsIn(m[0]) >= 10) return "phone number";
  if (NHS_NUMBER.test(raw)) return "NHS-number-shaped digits";
  if (DOB.test(raw)) return "date of birth";
  if (POSTCODE.test(raw)) return "postcode";
  const s = raw.replace(/\]\([^)]*\)/g, " ").replace(/[*_`~"'“”‘’[\](){}<>]+/g, " ").replace(/\s+/g, " ");
  if (HONORIFIC.test(s)) return "named person";
  if (PATIENT_WORD.test(s)) return "mentions a patient";
  return null;
}

export class PrivacySkip extends Error {}

/**
 * The only way out to the network. Screens the body, and latches any HTTP or network
 * failure so a request retried to success after a 429 still fails open.
 */
export function guardedFetch(realFetch, status) {
  return async (url, init = {}) => {
    const reason = patientDataReason(init.body);
    if (reason) {
      status.skipped = reason;
      throw new PrivacySkip("privacy screen");
    }
    try {
      const res = await realFetch(url, init);
      if (!res.ok) status.failed = `HTTP ${res.status}`;
      return res;
    } catch (err) {
      status.failed = err?.name === "AbortError" || err?.name === "TimeoutError" ? "timeout" : "network error";
      throw err;
    }
  };
}

// ---------------------------------------------------------------------------
// Key

export function keychainKey(exec = execFileSync) {
  try {
    return exec("security", ["find-generic-password", "-s", "jev-api-key", "-w"],
      { encoding: "utf8", timeout: 3000, stdio: ["ignore", "pipe", "ignore"] }).trim() || null;
  } catch { return null; }
}

// Private by construction (Astra rounds 1-15 showed any "spot the patient data" screen
// misses some phrasing). Only words in vocab.json leave the laptop: lowercase words that
// are in the system dictionary's lowercase entries (so not proper names) AND common in
// real worker messages, minus clinical and personal vocabulary (eval/build-vocab.mjs).
// A capitalised word survives only as a common sentence starter. Everything else, including
// every number, id, date and name, becomes "…". Jev judges status language only.
export function loadVocab(url) {
  try { return new Set(JSON.parse(readFileSync(url, "utf8"))); } catch { return new Set(); }
}
const TECH = new Set(["npm", "npx", "tsc", "jest", "vitest", "eslint", "lint", "typecheck", "qa", "ci", "pg", "api", "ui", "sql", "bash", "node", "git"]);
const STARTERS = new Set(("done fixed next still now once then also both no nothing yes not all the this that these it we i " +
  "waiting running tests test branch bead round ready blocked pushed merged left remaining before after when if while " +
  "because so but and or summary status result evidence warnings note notes none changes step phase verified").split(" "));
export function makeRedact(VOCAB) {
  return function redact(text) {
  const out = String(text || "").replace(/[A-Za-z]+|\n|[.,:;!?()-]|[^A-Za-z\n.,:;!?()-]+/g, (tok) => {
    if (/^[a-z]+$/.test(tok)) return VOCAB.has(tok) || TECH.has(tok) ? tok : "…";
    if (/^[A-Z][a-z]+$/.test(tok)) { const l = tok.toLowerCase(); return STARTERS.has(l) && (VOCAB.has(l) || TECH.has(l)) ? tok : "…"; }
    if (/^[A-Z]+$/.test(tok)) return ["QA", "CI", "PASS", "FAIL", "OK", "API", "UI"].includes(tok) ? tok : "…";
    if (/^(?:\n|[.,:;!?()-])$/.test(tok)) return tok;
    return /^\s+$/.test(tok) ? " " : "…";
  });
  return out.replace(/…(?:[\s…]*…)?/g, "…").replace(/[ \t]+/g, " ").trim();
  };
}

