// Builds vocab.json: lowercase words that are BOTH in the system dictionary (lowercase
// entries only, so proper names are excluded) AND used in real worker closing messages.
import { readFileSync, writeFileSync, globSync } from "node:fs";
import { homedir } from "node:os";
import { parseJsonl } from "../vendor/belay.mjs";
const dict = new Set(readFileSync("/usr/share/dict/words", "utf8").split("\n").filter((w) => /^[a-z]+$/.test(w)));
const rows = readFileSync("/Users/shane/Documents/Obsidian/Aestheticc/Ops/FLEET_MANIFEST.jsonl", "utf8").split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter((r) => r?.session_id);
const freq = new Map();
for (const r of rows) {
  const [p] = globSync(`${homedir()}/.claude/projects/*/${r.session_id}.jsonl`); if (!p) continue;
  for (const rec of parseJsonl(readFileSync(p, "utf8"))) {
    if (rec.type !== "assistant" || !Array.isArray(rec.message?.content)) continue;
    for (const b of rec.message.content) if (b?.type === "text") for (const w of (b.text || "").match(/\b[a-z]{2,}\b/g) || []) freq.set(w, (freq.get(w) || 0) + 1);
  }
}
// Frequent across the fleet (>= 25 uses) and a dictionary word. Clinical vocabulary is excluded.
const clinical = /^(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|monday|tuesday|wednesday|thursday|friday|saturday|sunday|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth|twenty|thirty|dob|birth|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|oh|nil|skin|face|facial|body|red|redness|rash|itch|itchy|sore|eye|eyes|nose|mouth|chin|jaw|forehead|neck|arm|leg|breast|hair|botox|toxin|filler|fillers|dermal|lip|lips|bruise|bruising|bruised|swelling|swollen|reaction|reactions|necrosis|occlusion|hyaluronidase|anaphylaxis|contraindication|contraindications|pregnant|pregnancy|allergy|allergies|allergic|prescription|prescriptions|prescribe|prescribed|adverse|complication|complications|injected|injection|injections|numb|numbness|cheek|cheeks|dose|doses|units|ml|pain|painful|infection|infected|lump|lumps|scar|scarring|headteacher|aged|age|old|year|years|born|birthday|address|street|road|lane|avenue|mother|father|wife|husband|daughter|son|nurse|doctor|patient|patients)$/;
// Regular inflections of a dictionary word count (tests, pushed, reviewers, running).
const stems = (w) => [w, w.replace(/s$/, ""), w.replace(/es$/, ""), w.replace(/ies$/, "y"), w.replace(/ed$/, ""), w.replace(/ed$/, "e"),
  w.replace(/(\w)\1ed$/, "$1"), w.replace(/ing$/, ""), w.replace(/ing$/, "e"), w.replace(/(\w)\1ing$/, "$1"), w.replace(/ers?$/, ""), w.replace(/ers?$/, "e"), w.replace(/ly$/, "")];
const inDict = (w) => stems(w).some((x) => x.length >= 2 && dict.has(x));
const vocab = [...freq].filter(([w, n]) => n >= 25 && inDict(w) && !clinical.test(w) && !stems(w).some((x) => clinical.test(x))).map(([w]) => w).sort();
writeFileSync(new URL("../vocab.json", import.meta.url), JSON.stringify(vocab));
console.log("vocab size", vocab.length, "sample", vocab.slice(0, 40).join(" "));
