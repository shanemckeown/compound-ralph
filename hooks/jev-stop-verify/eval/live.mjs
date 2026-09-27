// Live Jev over gated real stops (privacy-screened). Records answers for offline tests.
import { readFileSync, writeFileSync } from "node:fs";
import { QUESTIONS, ask, decide } from "../vendor/belay.mjs";
import { guardedFetch, keychainKey, MODEL, THRESHOLD, patientDataReason } from "../hook.mjs";
const stops = JSON.parse(readFileSync(new URL("./stops.json", import.meta.url)));
const key = keychainKey(); if (!key) { console.error("no key"); process.exit(1); }
const out = []; let blocked = 0, skipped = 0, errors = 0;
for (const s of stops) {
  const status = {};
  if (patientDataReason(s.state)) { skipped++; continue; }
  try {
    const a = await ask(s.state, QUESTIONS, { env: { JEV_API_KEY: key, JEV_MODEL: MODEL }, fetchImpl: guardedFetch(fetch, status) });
    const ev = { mutations: s.mutations, checks: s.checks, checksBeforeMutation: 0 };
    const v = decide(a.answers, ev, { threshold: THRESHOLD });
    if (v.block) blocked++;
    out.push({ id: s.id, state: s.state, checks: s.checks, mutations: s.mutations, answers: a.answers, block: v.block, model: a.model });
  } catch (e) { if (status.skipped) skipped++; else errors++; }
}
writeFileSync(new URL("./live.json", import.meta.url), JSON.stringify(out, null, 1));
console.log(JSON.stringify({ total: stops.length, answered: out.length, blocked, skipped, errors }));
