# CHANGE-NOTE — LUCY-hvryu

Backend only — no user-facing surface. Grepped the bead title/description/acceptance
criteria for UI-signal words (UI, frontend, screen, page, dashboard, studio, portal, flow,
"for users", "let clients/users", client-facing) and found none.

This change doesn't touch the Aestheticc clinic product at all — it edits the internal
instructions Claude Code agents follow when running `/goal` and `/long-goal` (in
`~/.claude/commands/`, a separate global-tooling repo, not `AestheticcNext` or the
Obsidian vault). No clinic, practitioner, or client ever sees this file or is affected by
it. Nothing to test in the product; the "test" is the process description in PLAN.md
(this run itself exercised the new instructions live while building the fix).
