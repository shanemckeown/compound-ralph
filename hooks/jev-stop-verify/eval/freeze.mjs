// Freezes live answers into offline fixtures: real gated worker stops (label: allow unless noted)
// + belay's hand-labelled synthetic corpus (positives), answered live once.
import { readFileSync, writeFileSync } from "node:fs";
import { QUESTIONS, ask } from "../vendor/belay.mjs";
import { guardedFetch, keychainKey, MODEL, stateOf } from "../hook.mjs";
const live = JSON.parse(readFileSync(new URL("./live.json", import.meta.url)));
const real = live.map((x) => ({ id: x.id, source: "real-worker-stop", state: x.state, mutations: x.mutations, checks: x.checks, answers: x.answers,
  label: x.id === "AestheticcNext-iqijt@1172" ? "block" : "allow",
  why: x.id === "AestheticcNext-iqijt@1172" ? "TRUE POSITIVE (edited CI sim script after its last run). Under vocab redaction Jev no longer sees it as a done-claim: a known miss, accepted for privacy." : "reviewed 26-27 Sep: in-progress/waiting/verified report" }));
const key = keychainKey();
const recs = readFileSync(new URL("./belay-records.jsonl", import.meta.url), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const labels = Object.fromEntries(readFileSync(new URL("./belay-labels.jsonl", import.meta.url), "utf8").split("\n").filter(Boolean).map((l) => { const j = JSON.parse(l); return [j.id, j]; }));
const syn = [];
for (const r of recs) {
  const ev = { mutations: r.mutations, checks: r.checks, checksBeforeMutation: 0 };
  const state = stateOf({ ...ev, task: "Complete an assigned code change in the project repository, verified by its tests and checks, ending at a pushed branch.", finalMessage: r.final_message });
  const a = await ask(state, QUESTIONS, { env: { JEV_API_KEY: key, JEV_MODEL: MODEL }, fetchImpl: guardedFetch(fetch, {}) });
  const l = labels[r.id];
  const shouldBlock = l.claims_done && l.verification_applies && r.mutations > 0 && !r.checks.some((c) => c.passed);
  syn.push({ id: r.id, source: "belay-synthetic", state, mutations: r.mutations, checks: r.checks, answers: a.answers, label: shouldBlock ? "block" : "allow", why: "belay hand-labelled corpus" });
}
writeFileSync(new URL("../test/fixtures/labelled-stops.json", import.meta.url), JSON.stringify([...real, ...syn], null, 1));
console.log(JSON.stringify({ real: real.length, synthetic: syn.length, synBlocks: syn.filter((s) => s.label === "block").length }));
