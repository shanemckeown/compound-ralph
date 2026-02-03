# Testing Guide: {{BEAD_ID}} - {{BEAD_TITLE}}

> **Purpose:** Two-phase testing strategy ensuring quality before merge.
> **Phase 1:** Automated tests (Ralph executes)
> **Phase 2:** Manual tests (Human executes after Ralph completes)

---

## Testing Philosophy

Ralph delivers a **fully working feature** by:
1. Writing and passing automated tests for all verifiable behavior
2. Documenting what requires manual human verification
3. Completing only when ALL Phase 1 tests pass

**A Ralph completes when only mandatory manual human testing remains.**

---

## Phase 1: Automated Tests (Ralph Executes)

These tests run via `npm test` and verify code correctness without human intervention.

### Test Coverage

| Area | Test File | What It Verifies |
|------|-----------|------------------|
| {{AREA_1}} | `__tests__/{{path}}.test.ts` | {{DESCRIPTION}} |
| {{AREA_2}} | `__tests__/{{path}}.test.ts` | {{DESCRIPTION}} |
| {{AREA_3}} | `__tests__/{{path}}.test.ts` | {{DESCRIPTION}} |

### Running Phase 1 Tests

```bash
# All tests for this feature
npm run test -- --testPathPattern={{FEATURE_NAME}}

# With coverage
npm run test -- --testPathPattern={{FEATURE_NAME}} --coverage
```

### Acceptance Criteria (Machine-Verifiable)

Each prd.json task should have acceptance criteria like:
- `Run \`npm run test -- --testPathPattern={{FEATURE_NAME}}\` - exits with code 0`
- `Run \`npm run lint -- --quiet\` - exits with code 0`

### Phase 1 Sign-Off

| Test Suite | Status | Notes |
|------------|--------|-------|
| Unit tests pass | ☐ | |
| Integration tests pass | ☐ | |
| Lint passes | ☐ | |
| Type-check passes | ☐ | |

**Phase 1 complete when all boxes checked.**

---

## Phase 2: Manual Tests (Human Executes)

These require real accounts, real browsers, or human judgment that cannot be automated.

> ⚠️ **These tests are BATCHED** - execute after ALL integration Ralphs complete, not after each one.

### Prerequisites

Before testing, ensure:
- [ ] Real {{SERVICE_NAME}} developer account created
- [ ] OAuth credentials configured in `.env.local`
- [ ] Dev server running (`npm run dev`)
- [ ] Logged into Aestheticc as business owner
- [ ] {{OTHER_PREREQUISITES}}

### Test Scenarios

#### Scenario 1: {{HAPPY_PATH_NAME}}

**Goal:** {{WHAT_USER_ACHIEVES}}
**Starting point:** {{URL_OR_SCREEN}}

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | {{ACTION}} | {{EXPECTED}} |
| 2 | {{ACTION}} | {{EXPECTED}} |
| 3 | {{ACTION}} | {{EXPECTED}} |

**Pass criteria:** {{FINAL_STATE}}

#### Scenario 2: {{ERROR_PATH_NAME}}

**Goal:** {{WHAT_USER_ACHIEVES}}
**Starting point:** {{URL_OR_SCREEN}}

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | {{ACTION}} | {{EXPECTED}} |
| 2 | {{ACTION}} | {{EXPECTED}} |

**Pass criteria:** {{FINAL_STATE}}

### Edge Cases

Test these error conditions:
- [ ] **No credentials:** Feature gracefully shows "not connected"
- [ ] **Invalid credentials:** Clear error message displayed
- [ ] **Service unavailable:** Appropriate fallback behavior
- [ ] **Permissions:** Non-owner cannot access settings (if applicable)

### Phase 2 Sign-Off

| Scenario | Pass | Tester | Date |
|----------|------|--------|------|
| {{SCENARIO_1}} | ☐ | | |
| {{SCENARIO_2}} | ☐ | | |
| Edge cases | ☐ | | |

---

## Merge Criteria

**Both phases must pass before merging to main:**

- [ ] Phase 1: All automated tests pass
- [ ] Phase 2: All manual scenarios verified
- [ ] No console errors during flows
- [ ] Mobile responsive (if applicable)

---

## Issues Found

| Issue | Severity | Status | Notes |
|-------|----------|--------|-------|
| | | | |

---

## Why Two Phases?

1. **Ralph efficiency** - Automated tests catch bugs without human time
2. **Human time batching** - Manual tests for all integrations run together
3. **Clear completion criteria** - Ralph knows when it's done
4. **Quality assurance** - Nothing ships without verification

---

*Template version: 2.0 (Two-Phase Testing)*
