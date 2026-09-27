import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os"; import { join } from "node:path";
import { run } from "../hook.mjs";
const W = "99999999-0000-0000-0000-000000000000", cwd = "/Users/shane/Documents/GitReBase/AestheticcNext";
const t = (i) => `2026-09-26T23:00:0${i}.000Z`;
const lines = [
  { type: "user", promptId: "p", timestamp: t(0), message: { role: "user", content: "<command-name>/goal</command-name>\n<command-args>AestheticcNext-smoke</command-args>" } },
  { type: "assistant", timestamp: t(1), message: { role: "assistant", content: [{ type: "tool_use", id: "a", name: "Edit", input: { file_path: `${cwd}/lib/booking/reschedule.ts` } }] } },
  { type: "user", timestamp: t(2), toolUseResult: { stdout: "" }, message: { role: "user", content: [{ type: "tool_result", tool_use_id: "a" }] } },
  { type: "assistant", timestamp: t(3), message: { role: "assistant", content: [{ type: "text", text: "Done. The reschedule fix is implemented and working; the bead is complete." }] } },
];
const dir = mkdtempSync(join(tmpdir(), "jsv-smoke-")); const p = join(dir, `${W}.jsonl`);
writeFileSync(p, lines.map((l) => JSON.stringify(l)).join("\n"));
const r = await run({ stdin: JSON.stringify({ session_id: W, transcript_path: p, cwd }), manifestText: JSON.stringify({ session_id: W }), guard: { read: () => ({}), record: () => {} }, settleMs: 0 });
console.log(JSON.stringify({ exit: r.exit, why: r.why, reason: r.reason }));
