import { readFileSync, globSync } from "node:fs";
import { homedir } from "node:os";
import { parseJsonl, classifyToolResult } from "../vendor/belay.mjs";
import { evidenceFromRecords, isCodeChange } from "../hook.mjs";
const [sid, idx] = process.argv.slice(2);
const [path] = globSync(`${homedir()}/.claude/projects/*/${sid}.jsonl`);
const recs = parseJsonl(readFileSync(path, "utf8"));
const prefix = recs.slice(0, Number(idx));
const ev = evidenceFromRecords(prefix, path, "/Users/shane/Documents/GitReBase/AestheticcNext", { until: recs[Number(idx)].timestamp });
console.log({ mutations: ev.mutations, checks: ev.checks.length, cbm: ev.checksBeforeMutation, last: ev.checks.slice(-3) });
// where are the code mutations
const pending = new Map(); let n = 0;
for (const l of prefix) {
  if (l.type === "assistant" && Array.isArray(l.message?.content)) for (const b of l.message.content) if (b.type === "tool_use") pending.set(b.id, b);
  if (l.type === "user" && l.toolUseResult) {
    const ref = (l.message.content || []).find?.((b) => b.type === "tool_result"); const c = pending.get(ref?.tool_use_id); n++;
    if (c && ["Write","Edit","MultiEdit"].includes(c.name) && isCodeChange(c.input)) console.log("MUT", n, c.input.file_path.split("/").slice(-3).join("/"), l.timestamp);
  }
}
