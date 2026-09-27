// Every real stop (system/turn_duration) in recent /goal worker sessions, through the gate. No network.
import { readFileSync, globSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { needsDoneCheck, freshChecks, parseJsonl } from "../vendor/belay.mjs";
import { evidenceFromRecords, patientDataReason, stateOf, MANIFEST } from "../hook.mjs";
const rows = readFileSync(MANIFEST, "utf8").split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter((r) => r?.session_id && /goal/.test(r.command || ""));
const want = Number(process.argv[2] || 40);
const out = []; const stats = { sessions: 0, stops: 0, gated: 0, privacy: 0 };
for (const r of rows.reverse()) {
  const [path] = globSync(`${homedir()}/.claude/projects/*/${r.session_id}.jsonl`);
  if (!path) continue;
  const recs = parseJsonl(readFileSync(path, "utf8"));
  stats.sessions++;
  recs.forEach((rec, i) => {
    if (rec.type !== "system" || rec.subtype !== "turn_duration") return;
    stats.stops++;
    const prefix = recs.slice(0, i);
    const ev = evidenceFromRecords(prefix, path, r.cwd, { until: rec.timestamp });
    if (!needsDoneCheck(ev)) return;
    stats.gated++;
    const state = stateOf(ev);
    const priv = patientDataReason(JSON.stringify(state));
    if (priv) stats.privacy++;
    out.push({ id: `${r.bead}@${i}`, bead: r.bead, state, privacy: priv, mutations: ev.mutations, checks: freshChecks(ev) });
  });
  if (out.length >= want) break;
}
console.error(JSON.stringify(stats));
writeFileSync(new URL("./stops.json", import.meta.url), JSON.stringify(out, null, 1));
