# /take — Claim Manager Work from a Handoff

Use this command to make an already-open Agent View tab the single live Manager for one handoff document, then load that document as its full brief.

## Usage

```text
/take <path-to-handoff-doc.md>
/take Ops/MANAGER_INFRA_HANDOFF_2026-08-27.md
```

The path may be absolute or relative to the vault's `Aestheticc/` root at `/Users/shane/Documents/Obsidian/Aestheticc/`.

## 1. Resolve the document and derive the work ID

Resolve a relative argument against `/Users/shane/Documents/Obsidian/Aestheticc/` and use that absolute path as `HANDOFF_DOC`. Derive `WORK_ID` mechanically from the resolved document's filename stem: take the basename and remove the final `.md`. Do not infer, abbreviate, or negotiate another ID; the filename stem is the shared ID the orchestrator and this tab use.

## 2. Reuse this session's ID

Use the session ID already surfaced by `~/.claude/hooks/fleet-role-sessionstart.sh` in this session's SessionStart `additionalContext`. It appears inside the suggested `fleet-role.py <session-id> ...` command in that context. Reuse that exact ID; do not re-derive it.

If that context is genuinely unavailable, extract the UUID path component from the scratchpad directory shown in the system prompt's **Scratchpad Directory** section. Its shape is `/private/tmp/claude-<pid>/<project>/<SESSION-UUID>/scratchpad`; the directory immediately before `scratchpad` is the fallback session ID.

## 3. Claim this work

Run:

```bash
python3 ~/.claude/scripts/fleet-role.py manager "$WORK_ID" "$SESSION_ID" --claim --handoff-doc "$HANDOFF_DOC"
```

If the command refuses because another live session holds this exact work ID, **STOP** and surface the conflict to the user. Never add `--steal` automatically: a live Manager on the same work is a real conflict. Retry with `--steal` only after the human running `/take` confirms that the reported live claim is wrong or stale, following the existing ORCHESTRATOR `--claim`/`--steal` convention in `Aestheticc/CLAUDE.md`.

## 4. Load and execute the handoff

Read the resolved handoff document in full. It is the brief: proceed from it exactly as if the orchestrator had delivered the same content through a live `SendMessage`, with the same trust level. Escalate genuine design forks back to the orchestrator through `SendMessage`; do not guess.

Read and follow `Aestheticc/CLAUDE.md`'s **Orchestrator vs sub-Claude** section for the obligations attached to the Manager role.

## 5. Preserve the fallback

This command removes the need for the orchestrator to `SendMessage`-load a full brief into an already-open tab; that interim pattern remains valid unchanged for older sessions or work that predates its handoff document.
