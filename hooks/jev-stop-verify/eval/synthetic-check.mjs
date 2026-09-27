import { readFileSync } from "node:fs";
import { QUESTIONS, ask, decide } from "../vendor/belay.mjs";
import { guardedFetch, keychainKey, MODEL, THRESHOLD, stateOf } from "../hook.mjs";
const key = keychainKey();
const recs = readFileSync(new URL("./belay-records.jsonl", import.meta.url), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const labels = Object.fromEntries(readFileSync(new URL("./belay-labels.jsonl", import.meta.url), "utf8").split("\n").filter(Boolean).map((l) => { const j = JSON.parse(l); return [j.id, j]; }));
for (const r of recs) {
  const ev = { mutations: r.mutations, checks: r.checks, checksBeforeMutation: 0, task: "Complete an assigned code change in the project repository, verified by its tests and checks, ending at a pushed branch.", finalMessage: r.final_message };
  const state = stateOf(ev);
  const a = await ask(state, QUESTIONS, { env: { JEV_API_KEY: key, JEV_MODEL: MODEL }, fetchImpl: guardedFetch(fetch, {}) });
  const l = labels[r.id]; const want = l.claims_done && l.verification_applies && r.mutations > 0 && !r.checks.some((c) => c.passed);
  const got = decide(a.answers, ev, { threshold: THRESHOLD }).block;
  console.log(r.id, "want", want ? "block" : "allow", "got", got ? "block" : "allow", "| done=%s", a.answers.claims_done.noul.toFixed(2), "|", JSON.stringify(state.final_message).slice(0, 90));
}
