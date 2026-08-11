# /orchestra — Orchestrator-mode boot for intertwining epics

Call this when you are about to manage several epics that touch each other and you need one chat to act as an orchestrator (plan, sequence, dispatch Agent View sessions) instead of writing code itself. It force-loads the orchestration contract, claims a scope, pulls the live picture from git + beads, and then operates by the rules for the rest of the session.

This is the orchestrator's `/morning`: one command boots the whole cockpit. It removes the need to remember the protocol — you remember one word, the chat loads the rest.

## Usage

```
/orchestra                      # interactive: shows the partition + open epics, asks which scope to take
/orchestra B                    # boot as orch-B (the sensitive lane: agency / receptionist / multi-tenancy / payments)
/orchestra A                    # boot as orch-A (correctness / promise-audit / forms)
/orchestra C                    # boot as orch-C (comms / strategy / customer / vault LUCY beads)
/orchestra rmc8o sj5mb abyum    # ad-hoc: orchestrate exactly these epics this session
/orchestra --status             # read-only map of who-owns-what + what's in flight, claim nothing
```

## Step 1 — Load the contract (mandatory)

Read the protocol in full before anything else. Everything below is enforced by it:

```
Read /Users/shane/Documents/Obsidian/Aestheticc/ORCHESTRATION_PROTOCOL.md
```

The three rules that matter most: **relay-ban** (rulings live on beads, never carried between chats by Shane), **partition by epic** (you only rule on beads you own), **premise-gate before dispatch** (re-confirm still-broken on today's `main`).

## Step 2 — Establish scope

- If given a role letter (A/B/C): map it to the partition table at the bottom of the protocol. Set `assignee` on the owned epics so the rails stay true: `bd update AestheticcNext-<epic> --assignee=orch-<X>`.
- If given epic/bead IDs: that is your scope this session (ad-hoc). Confirm them with `bd show`.
- If no args: print the current partition + `bd list --type=epic --status=open`, then ask which scope to take. Do not guess.

`--status` mode stops after producing the map (Step 5) and claims nothing.

## Step 3 — Pull the live picture

Run from `/Users/shane/Documents/GitReBase/AestheticcNext` (orch-C also checks the vault `.beads` for LUCY beads):

1. **Your queue:** `bd list --assignee=orch-<X> --status=open` and `--status=in_progress` (or `bd show` each owned epic + children for ad-hoc scope).
2. **The decision queue:** `bd list --label=needs-shane` — what only Shane can clear. Surface it; do not re-ask anything already here.
3. **Git truth (what's actually done):** run the reconcile dry-run so you start from reality, not from possibly-stale bead status:
   ```bash
   bun scripts/ops/bead-merge-reconcile.mjs        # dry-run: merged / pending / dirty per branch+bead
   ```
   If that script does not exist yet, fall back to the inline branch check: `git rev-list --count origin/main..<branch>` == 0 means the branch is merged and safe to close. A three-dot empty diff can false-positive on reverted/no-op branches, so it is only a squash-suspect hint, not sufficient alone. Treat **git merge-status as the only "done" signal** — bead status and Agent View "working/completed" both lie (a session can show "working" while its branch merged hours ago).
4. **Cross-epic blocks:** which of your beads depend on beads owned by another orchestrator (`bd show` dependencies). Those are dependencies in the graph, not chat relays.

## Step 4 — Premise-gate the ready set

Before you call anything dispatchable, re-confirm it is still broken on today's `main` (grep the code, quick `/browse` where relevant, still-wanted check). Drop stale ones with a one-line note + `premise-stale` label. A "NOT-STARTED" bead is not proof the work is still needed — shipped-but-not-closed beads are the main source of duplicate work.

## Step 5 — Emit the orientation briefing

A compact block:

```
ORCHESTRA — orch-B  (agency / receptionist / multi-tenancy / payments)
Epics: rmc8o, abyum, awsue, f8us

Ready (premise-checked): 3   ·   In flight: 2   ·   Need Shane: 1
Git truth: 2 beads merged-but-open (close candidates) · 1 closed-but-unmerged (WARN) · 1 worktree dirty (do not touch)
Blocked on: orch-A's f9dnj.1.2 (POS idempotency) before sj5mb can land
Decision queue: 8urhn — receptionist guard payment-access decision (your call needed)
```

## Step 6 — Operate by the rules for the rest of the session

- **Relay-ban:** every ruling you make → write it to the bead immediately (label + one-line comment). Never tell Shane to carry a decision to another chat. Read other orchestrators' rulings off their beads.
- **Lane every dispatch:** `auto-eligible` (safe to `/goal` and fire-and-forget) · `lane:reviewed` (Codex implements, Lucy reviews — mandatory for auth, payments, Stripe, terminal, multi-tenancy, GDPR, migrations) · `lane:human-gated` (`needs-shane` first).
- **Dispatch via** `/goal 'AestheticcNext-<id>'` — the session auto-names itself (UserPromptSubmit hook). File-distinct beads can run in parallel. Sessions push branches; they do not deploy.
- **Land only via** `/land-batch` — the single prod gate for the whole org, held to the deploy window.
- **Escalate via the queue:** anything for Shane gets `needs-shane` + a one-line answerable question on the bead, never a chat that rots for days.

## Relationship to other skills

- `/orchestra` = manage intertwining epics (this cockpit).
- `/goal 'AestheticcNext-<id>'` = execute one bead autonomously (what you dispatch into Agent View).
- `/dispatch` = fire the auto-bead pipeline.
- `/land-batch` = land finished branches → staging QA → one prod gate.

## Why this skill exists

Created 2026-06-15 after the "how do I orchestrate my orchestrators" session. Three orchestrator chats were colliding on shared epics, with Shane acting as the message bus between them. The protocol fixed the rules; this command makes them load on demand so Shane carries one word (`/orchestra`) instead of the whole rulebook. Pairs with the session-naming hook (`~/.claude/hooks/agentview-name-session.py`) and the merge-to-close reconcile (`scripts/ops/bead-merge-reconcile.mjs`, bead t4wfi).
