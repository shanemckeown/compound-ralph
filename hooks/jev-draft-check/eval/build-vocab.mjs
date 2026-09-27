// vocab.json for jev-draft-check: lowercase words in the system dictionary's lowercase
// entries (no proper names) that Shane's client replies and sent emails actually use,
// unioned with jev-stop-verify's vocab, minus clinical, personal, number and date words.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
const dict = new Set(readFileSync("/usr/share/dict/words", "utf8").split("\n").filter((w) => /^[a-z]+$/.test(w)));
const dirs = ["/Users/shane/Documents/Obsidian/Today's replies", "/Users/shane/Documents/Obsidian/Aestheticc/Growth/Outreach/SentEmails"];
const freq = new Map();
for (const d of dirs) for (const f of readdirSync(d).filter((x) => x.endsWith(".md"))) {
  for (const w of (readFileSync(join(d, f), "utf8").match(/\b[a-z]{2,}\b/g) || [])) freq.set(w, (freq.get(w) || 0) + 1);
}
const blocked = /^(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|monday|tuesday|wednesday|thursday|friday|saturday|sunday|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|twenty|thirty|dob|birth|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|oh|nil|skin|face|facial|body|red|redness|rash|itch|itchy|sore|eye|eyes|nose|mouth|chin|jaw|forehead|neck|arm|leg|breast|hair|botox|toxin|filler|fillers|dermal|lip|lips|bruise|bruising|bruised|swelling|swollen|reaction|reactions|necrosis|occlusion|hyaluronidase|anaphylaxis|contraindication|contraindications|pregnant|pregnancy|allergy|allergies|allergic|prescription|prescriptions|prescribe|prescribed|adverse|complication|complications|injected|injection|injections|injectable|injectables|numb|numbness|cheek|cheeks|dose|doses|units|ml|pain|painful|infection|infected|lump|lumps|scar|scarring|aged|age|old|year|years|born|birthday|address|street|road|lane|avenue|mother|father|wife|husband|daughter|son|nurse|doctor|patient|patients|medical|clinical|treatment|treatments|consultation|aftercare|wrinkle|wrinkles|peel|laser|microneedling|profhilo|anti|cold|colds|flu|fever|tablet|tablets|pill|pills|medication|medications|medicine|medicines|antibiotic|antibiotics|symptom|symptoms|diagnosis|diagnosed|condition|conditions|surgery|hospital|gp|sick|ill|illness|morning|evening|night|daily|weekly)$/;
const stems = (w) => [w, w.replace(/s$/, ""), w.replace(/es$/, ""), w.replace(/ies$/, "y"), w.replace(/ed$/, ""), w.replace(/ed$/, "e"), w.replace(/(\w)\1ed$/, "$1"), w.replace(/ing$/, ""), w.replace(/ing$/, "e"), w.replace(/(\w)\1ing$/, "$1"), w.replace(/ers?$/, ""), w.replace(/ers?$/, "e"), w.replace(/ly$/, "")];
const ok = (w) => stems(w).some((x) => x.length >= 2 && dict.has(x)) && !stems(w).some((x) => blocked.test(x));
const worker = JSON.parse(readFileSync(new URL("../../jev-stop-verify/vocab.json", import.meta.url), "utf8"));
// Rule vocabulary the checks need, all generic English.
const rules = "sorry apologies apologise apologize apology fault mistake delay delayed late later soon coming yet built building currently transparent honest honestly unfortunately call calls chat zoom jump hop quick meeting ring catch phone roadmap development developing planned plan working progress pipeline".split(" ");
const vocab = [...new Set([...worker.filter((w) => !blocked.test(w)), ...[...freq].filter(([w, n]) => n >= 3 && ok(w)).map(([w]) => w), ...rules])].sort();
writeFileSync(new URL("../vocab.json", import.meta.url), JSON.stringify(vocab));
console.log("draft vocab", vocab.length);
