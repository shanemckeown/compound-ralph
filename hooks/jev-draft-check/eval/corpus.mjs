// Real reply drafts + hand-written paraphrases -> phrase hits, patient risk, live Jev answers.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { QUESTIONS, MODEL, phraseHits, patientRisk, stateOf } from "../hook.mjs";
import { guardedFetch, keychainKey } from "../../jev-common/privacy.mjs";
const dir = "/Users/shane/Documents/Obsidian/Today's replies";
const real = readdirSync(dir).filter((f) => /- (?:Email|WhatsApp)\.md$/i.test(f) && !/REPORT/.test(f)).map((f) => ({ id: f, text: readFileSync(join(dir, f), "utf8") }));
const para = [
  ["para:blame1", "Hi Carley,\n\nI appreciate this took a while to sort out, that was an oversight on our part. The reminders now go out at 9am.\n\nKind regards,\nShane", "blame"],
  ["para:blame2", "Hi Tom,\n\nThat slipped through the net on our end, which isn't good enough. It's fixed now and the invoices match.\n\nShane", "blame"],
  ["para:build1", "Hi Leah,\n\nThe stock export is something we're putting the finishing touches on, so it should land in a few weeks.\n\nShane", "buildState"],
  ["para:build2", "Hi Nicole,\n\nThat part is still in the works on our side, so for now you'd need to do it by hand.\n\nShane", "buildState"],
  ["para:call1", "Hi Linzi,\n\nLet's find a time to talk it through together, it'll be easier than email.\n\nShane", "callOffer"],
  ["para:call2", "Hi Kelly,\n\nI can show you over video this week if you'd like, just let me know a time that suits.\n\nShane", "callOffer"],
  ["para:clean1", "Hi Chris,\n\nThe reminders now go out at 9am the day before. You'll find the setting under Settings, then Notifications.\n\nKind regards,\nShane", ""],
  ["para:clean2", "Hi Adela,\n\nYour booking page now shows all three practitioners, and each has their own hours.\n\nShane", ""],
].map(([id, text, label]) => ({ id, text, label }));
const key = keychainKey();
const out = [];
for (const d of [...real, ...para]) {
  const risk = patientRisk(d.text);
  let answers = null;
  if (!risk && key) {
    const res = await guardedFetch(fetch, {})("https://api.typesafe.ai/v1/systemone", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` }, body: JSON.stringify({ model: MODEL, state: stateOf(d.text), questions: QUESTIONS }) });
    answers = res.ok ? (await res.json()).answers : null;
  }
  const j = answers ? Object.fromEntries(Object.entries(answers).map(([k, v]) => [k, +v.noul.toFixed(2)])) : null;
  out.push({ id: d.id, label: d.label ?? null, phrases: phraseHits(d.text), risk, jev: j, sent: answers ? stateOf(d.text).draft : null });
  console.log(d.id.slice(0, 60).padEnd(60), "| phr:", phraseHits(d.text).join(",") || "-", "| risk:", risk || "-", "| jev:", j ? JSON.stringify(j) : "-");
}
writeFileSync(new URL("./corpus.json", import.meta.url), JSON.stringify(out, null, 1));
