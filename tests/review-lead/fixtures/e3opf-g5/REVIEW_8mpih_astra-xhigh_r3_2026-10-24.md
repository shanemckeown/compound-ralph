REVIEWING: goal/aestheticcnext-8mpih @ af5df710dab84821f86829af31a50fbbc7d3cca3
FAIL
Reviewer: Codex Astra gpt-6-astra xhigh, read-only, fresh session, independent round 3, 2026-10-24.

P2 lib/ava/unpaid-reply.ts:88 — A clinic owner using Ava on any device asks about 1–31 August. After an empty read, “Nobody owes you anything for bookings on 31 August” passes unchanged: “31 August” contains both endpoint strings, including “1 August”. The figure guard also permits 31 from the read dates. Ava therefore omits the actual checked range. — Match complete date tokens in an ordered range, or always supply the fixed dated line; add regression coverage for this substring collision.

VERIFIED OK

- r2 P2 lib/ava/unpaid-reply.ts:104 closed: traced the bare “Nobody owes you anything” reply to dated replacement or mixed-turn append; read both regression cases. Nonempty or failed unpaid reads retain null entries, preventing this fallback.
- r2 P2 lib/actions/read/unpaid-window.ts:49 closed: confirmed composition of ukDateParts, parseIsoDate, addDaysUtc and formatIsoDate. UK calendar extraction followed by UTC arithmetic preserves the previous date results across DST and year boundaries.

UNVERIFIED

Runtime behavior and test results: code and tests inspected only; no suites run. MAX_ROUNDS exhaustion and live.mjs wording excluded.

```review-verdict
{"verdict":"FAIL","findings":[{"priority":"P2","file":"lib/ava/unpaid-reply.ts","line":88,"description":"Substring matching lets '31 August' satisfy both endpoints of a checked 1–31 August range, accepting a reply that omits the actual checked window."}]}
```