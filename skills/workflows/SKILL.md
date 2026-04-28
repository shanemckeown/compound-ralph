# Skill: workflows

Route to the appropriate workflow phase for compound development.

## Trigger
- `/workflows:plan` - Plan a feature from description
- `/workflows:work` - Prep for Ralph execution (terminal handoff)
- `/workflows:review` - Multi-agent code review
- `/workflows:compound` - Extract and document learnings

## The Compound Cycle

```
   ┌─────────────────────────────────────────────────────────┐
   │                                                         │
   ▼                                                         │
┌──────┐     ┌──────┐     ┌────────┐     ┌──────────┐       │
│ Plan │ ──▶ │ Work │ ──▶ │ Review │ ──▶ │ Compound │ ──────┘
└──────┘     └──────┘     └────────┘     └──────────┘
   │            │             │               │
   ▼            ▼             ▼               ▼
 bead       terminal        PR/fixes      LEARNINGS.md
            command
```

Each cycle compounds: plans inform future plans, reviews catch more issues, patterns get documented.

**Important:** The Work phase requires a **terminal handoff** — Ralph runs as external bash scripts that spawn fresh Claude processes, not as a plugin command.

---

## /workflows:plan

**Purpose**: Turn feature ideas into beads with acceptance criteria

**Input**: Feature description, user feedback, or bug report

**Process**:
1. Analyze the request
2. Create bead with `bd create`
3. Add design notes and acceptance criteria
4. Identify dependencies on other beads

**Output**: Bead ID ready for Ralph execution

---

## /workflows:work

**Purpose**: Execute bead via Ralph (autonomous implementation)

**Input**: Bead ID

**Process**:
1. Verify bead exists and has good specs (via MCP tools if bd CLI bugs)
2. Check for blockers/dependencies
3. Create git worktree + npm install
4. Generate `.compound` files via Task agent (spec.md, prd.json, RALPH_PROMPT.md, etc.)
5. Kick off Ralph with `--skip-setup --background`
6. Confirm Ralph is running (check tmux session)

**Execution:**
```bash
# Lucy creates worktree
cd /Users/shane/Documents/GitReBase/AestheticcNext
git worktree add ~/.worktrees/AestheticcNext/{BEAD_ID} -b feature/{BEAD_ID}-{slug} origin/main
cd ~/.worktrees/AestheticcNext/{BEAD_ID} && npm install

# Lucy generates .compound files via Task agent
# (analyzes codebase, creates spec.md, prd.json, RALPH_PROMPT.md, etc.)

# Lucy kicks off Ralph
~/Documents/GitReBase/compound-ralph/ralph-bead.sh {BEAD_ID} --skip-setup --background
```

**Note:** If `bd` CLI has stack overflow issues, use MCP tools (`mcp__plugin_beads_beads__show`) to validate beads instead.

**Monitoring:**
```bash
# Attach to tmux session
tmux attach -t ralph-{BEAD_ID}

# View logs
tail -f ~/.worktrees/AestheticcNext/{BEAD_ID}/.compound/logs/latest.log

# Check task progress
jq '.tasks[] | {id, title, passes}' ~/.worktrees/AestheticcNext/{BEAD_ID}/.compound/prd.json

# Graceful stop
touch ~/.worktrees/AestheticcNext/{BEAD_ID}/.compound/STOP
```

---

## /workflows:review

**Purpose**: Multi-perspective code review before merge

**Input**: PR number or branch name

**Process**:
1. **Security Review** - Check for vulnerabilities, injection, auth issues
2. **Architecture Review** - Check patterns, coupling, scalability
3. **Code Quality** - TypeScript strictness, error handling, edge cases
4. **UX Review** - For UI changes, check accessibility, mobile, loading states

Each review perspective is a separate agent pass. Findings are:
- Fixed immediately if simple
- Added as follow-up tasks if complex
- Documented in PR comments

**Output**: Approved PR or list of required fixes

---

## /workflows:compound

**Purpose**: Extract learnings to improve future work

**Input**: Completed bead ID or PR

**Process**:
1. Read `.compound/LEARNINGS.md` from the PR branch
2. Identify reusable patterns (not bead-specific)
3. Append to `~/.claude/AESTHETICC_LEARNINGS.md`
4. Update any relevant CLAUDE.md sections
5. Close the bead with summary

**Output**: Updated global learnings file

---

## Quick Reference

```bash
# Full cycle
/workflows:plan "Add email verification to signup"
# → Creates AestheticcNext-xyz, specs it out

/workflows:work AestheticcNext-xyz
# → Lucy creates worktree, generates .compound files, kicks off Ralph
# → Ralph runs in background tmux session

# Monitor while Ralph works:
tmux attach -t ralph-AestheticcNext-xyz
# Or: tail -f ~/.worktrees/AestheticcNext/AestheticcNext-xyz/.compound/logs/latest.log

# After Ralph completes:
/workflows:review 123
# OR /workflows:review AestheticcNext-xyz
# → Reviews PR, fixes issues

/workflows:compound AestheticcNext-xyz
# → Extracts learnings, closes bead
```

---

## Integration with Beads

The workflow commands integrate with beads:

| Phase | Bead Status | Who Runs It |
|-------|-------------|-------------|
| Plan | Creates bead (open) | Lucy |
| Work | Sets in_progress | Lucy kicks off → Ralph executes in tmux |
| Review | Stays in_progress | Lucy |
| Compound | Sets closed | Lucy |

---

## Notes

- Each phase can be run independently
- Work phase: Lucy orchestrates setup + kicks off Ralph in background tmux
- If `bd` CLI has stack overflow, use MCP tools instead
- Review phase is optional but recommended for complex changes
- Compound phase ensures nothing learned is lost
- For full feedback-to-feature automation, see `/R2F`
