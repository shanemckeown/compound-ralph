# MCP tiers — what each kind of Claude session is allowed to load

MCP servers are spawned **at session launch, not on first use**. So the cost of a
server is paid by every session that has it in scope, whether or not a single tool
is ever called. The lever is therefore what gets *loaded*, not what gets used —
which is why this is config, not discipline.

`--mcp-config <file> --strict-mcp-config` loads **only** the named servers and
ignores `~/.claude.json` (global) and any project `.mcp.json`.

## The tiers

| Tier | Who | Servers | Set by |
|---|---|---|---|
| **A — code worker** | spawned `/goal` + `/long-goal` sub-Claudes | **none** | `fleet-dispatch.py` (`tier-a-code-worker.json`) |
| **B — code chat** | Shane's interactive AestheticcNext session | current set, incl. gbrain | unchanged, Shane's call |
| **C — marketing/ops chat** | vault sessions | current full set | unchanged, Shane's call |

Only Tier A is enforced here. B and C are Shane's own working sessions and his
working set is his decision — the option was offered, not applied.

## Why Tier A is empty, and the evidence for it

Verified 2026-08-14 before the change, twice and independently:

1. `/goal` and `/long-goal` contain **no `mcp__` reference**. The single grep hit
   for "stripe" is the file path `lib/stripe/` inside a refusal criterion, not the
   Stripe MCP server.
2. The skills those two invoke — `/autoplan`, `/codex`, `/review`, `/ship`,
   `/plan-build-qa`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-devex-review` —
   were then grepped as a chain, because "the skill doesn't mention MCP" would not
   survive one of them calling something that does. **The only `mcp__` references
   anywhere in the chain are `mcp__conductor__`.** Conductor was replaced by Agent
   View on 2026-05-31 and `CLAUDE.md` says to ignore lingering references to it.
   They are dead references to a retired tool.

So this is empty on evidence, not on "probably fine".

A code worker that genuinely needs graph traversal **escalates to its orchestrator**
— that is already the documented sub-Claude behaviour, so the route exists without
paying for a tunnel in every session.

## Why the cut stops at Tier A

**Cut where it multiplies; leave the singletons.** Tier A is ~10 concurrent
sessions, so each server removed is saved ten times over. Tiers B and C are one
session each — removing a tool Shane reaches for would save one tunnel and cost
friction on his attention, which is the genuinely scarce resource. Bad trade at
n=1, excellent at n=10.

## Measured

Per spawned session, before: **6 MCP servers + 2 persistent autossh tunnels** to
the Hermes VPS (sequential-thinking, stripe, outscraper, aestheticc-ops, sentry,
plus gbrain and hermes as the two tunnels). After: **0 and 0**.

## If you are about to add a server here

Don't, unless you have re-run the two-hop grep above and it now finds a live
`mcp__` call in the `/goal` chain. An unexplained restriction gets undone by the
next person who assumes it was cautious rather than measured — that is what this
file exists to prevent.
