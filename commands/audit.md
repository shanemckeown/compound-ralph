# /audit - Continuous Codebase Audit

Continue the systematic line-by-line review of the AestheticcNext codebase. Picks up where you left off.

## Usage
```
/audit                    # Continue next module
/audit status             # Show progress
/audit [module]           # Audit specific module (e.g., /audit appointments)
/audit reset              # Reset and start from beginning
```

## Quick Start

When invoked, I will:
1. Read audit state from `CSAI/USER_GUIDES/AUDIT_STATE.json`
2. Identify the next unaudited module
3. Run a comprehensive audit producing 3 files
4. Update state and move to next module

## Audit Queue (Priority Order)

| # | Module | Key Files |
|---|--------|-----------|
| 1 | authentication | NextAuth, middleware, login/register |
| 2 | appointments | Calendar, booking flow, reminders |
| 3 | clients | Client CRUD, medical history, search |
| 4 | treatments | Treatment definitions, pricing |
| 5 | consent-forms | Form builder, signatures, storage |
| 6 | inventory | Products, batch tracking, expiry |
| 7 | face-mapping | Injection sites, unit tracking |
| 8 | payments | Checkout, Stripe, refunds |
| 9 | prescriptions | UK private prescriptions |
| 10 | ai-features | Content gen, advisor, voice notes |
| 11 | online-booking | Public booking, availability |
| 12 | settings | Clinic setup, user management |

## Output Per Module

Each audit produces 3 files in `CSAI/USER_GUIDES/[module]/`:

### 1. AUDIT.md
- Security review (auth checks, input validation, XSS, injection)
- Schema verification (types, relations, constraints)
- Connection audit (API routes → correct DB tables)
- Error handling review
- Performance check (N+1 queries, re-renders)
- Dead code identification
- Type safety review (no `any`, proper types)
- Test coverage gaps
- Mobile parity check

### 2. FILES.md
```markdown
| File | Purpose | Status | Issues |
|------|---------|--------|--------|
| /app/api/clients/route.ts | Client CRUD | ✅ | None |
| /components/ClientForm.tsx | Edit form | ⚠️ | Missing validation |
```

### 3. USER_GUIDE.md
Concise task-based instructions ONLY after audit confirms correctness:
```markdown
## Add a Client
1. Dashboard → Clients → "Add Client"
2. Enter name, email, phone
3. Click "Save"
```

## State File

Progress tracked in `CSAI/USER_GUIDES/AUDIT_STATE.json`:
```json
{
  "current_module": "appointments",
  "completed": ["authentication"],
  "in_progress": null,
  "last_updated": "2026-02-05T18:00:00Z",
  "issues_found": 3,
  "beads_created": ["AestheticcNext-xyz"]
}
```

## Execution Flow

```
┌─────────────────────────────────────────────────┐
│  1. READ STATE                                   │
│     Load AUDIT_STATE.json                        │
│     Identify next module in queue                │
└─────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│  2. AUDIT MODULE                                 │
│     - Glob all relevant files                    │
│     - Read each file line-by-line                │
│     - Check security, schema, connections        │
│     - Identify issues                            │
└─────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│  3. WRITE OUTPUTS                                │
│     - Create AUDIT.md with findings              │
│     - Create FILES.md with file status           │
│     - Create USER_GUIDE.md (if audit passes)     │
└─────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│  4. UPDATE STATE                                 │
│     - Mark module complete                       │
│     - Create beads for critical issues           │
│     - Move to next module                        │
└─────────────────────────────────────────────────┘
```

## Critical Issues → Beads

When audit finds P0/P1 issues:
1. Create bead immediately in AestheticcNext database
2. Link to audit findings
3. Add to `beads_created` in state
4. Security issues block until fixed

## Example Session

```
User: /audit

Lucy: 📊 Audit Status
      ✅ Completed: authentication
      🔄 Next up: appointments

      Starting audit of appointments module...

      [Runs comprehensive audit]

      ✅ Audit complete!

      📁 Created:
      - CSAI/USER_GUIDES/appointments/AUDIT.md
      - CSAI/USER_GUIDES/appointments/FILES.md
      - CSAI/USER_GUIDES/appointments/USER_GUIDE.md

      ⚠️ Issues found: 2
      - Missing null check in /app/api/appointments/route.ts:45
      - No test coverage for reminder sending

      Next module: clients
      Run /audit to continue
```

## Module File Patterns

### authentication
```
/app/api/auth/**/*
/lib/auth.ts
/middleware.ts
/app/(auth)/**/*
/components/auth/**/*
```

### appointments
```
/app/api/appointments/**/*
/app/(dashboard)/calendar/**/*
/components/calendar/**/*
/components/appointments/**/*
/lib/appointments/**/*
```

### clients
```
/app/api/clients/**/*
/app/(dashboard)/clients/**/*
/components/clients/**/*
/lib/clients/**/*
```

(See AUDIT_MODULE_PLAN.md for full patterns)

## Requirements

- Working directory must be Obsidian vault
- AestheticcNext repo accessible at standard location
- Beads MCP available for issue creation

## Related

- Epic: `LUCY-bya` (Continuous Codebase Audit System)
- Plan: `CSAI/CTO/AUDIT_MODULE_PLAN.md`
- Guides: `CSAI/USER_GUIDES/`
