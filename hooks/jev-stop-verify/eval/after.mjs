import { readFileSync, globSync } from "node:fs";
import { homedir } from "node:os";
import { parseJsonl } from "../vendor/belay.mjs";
import { isCodeChange } from "../hook.mjs";
const [bead, idx] = process.argv.slice(2);
const rows = readFileSync("/Users/shane/Documents/Obsidian/Aestheticc/Ops/FLEET_MANIFEST.jsonl","utf8").split("\n").filter(Boolean).map(l=>{try{return JSON.parse(l)}catch{return null}}).filter(r=>r?.bead===bead&&r.session_id);
for (const r of rows) {
  const [path] = globSync(`${homedir()}/.claude/projects/*/${r.session_id}.jsonl`); if (!path) continue;
  const recs = parseJsonl(readFileSync(path,"utf8")); if (recs.length < Number(idx)) continue;
  const pre = recs.slice(0, Number(idx)); const calls = [];
  for (const l of pre) if (l.type==="assistant"&&Array.isArray(l.message?.content)) for (const b of l.message.content) if (b.type==="tool_use") calls.push(b);
  let last = -1; calls.forEach((b,i)=>{ if(["Write","Edit","MultiEdit"].includes(b.name)&&isCodeChange(b.input)) last=i; });
  console.log("last code mutation:", calls[last]?.input?.file_path);
  for (const b of calls.slice(last+1)) console.log(b.name, b.input?.run_in_background?"[bg]":"", String(b.input?.command||b.input?.file_path||b.input?.description||b.input?.prompt||"").slice(0,140).replace(/\n/g," "));
  break;
}
