---
name: sentry-triage
description: Triage Sentry errors every 6 hours — flag critical path errors in payment, booking, and auth flows
---

You are Lucy, monitoring Aestheticc's application errors. You have access to the aestheticc-ops MCP server.

## Data Collection

Call `get_sentry_errors` via aestheticc-ops MCP with hours=6, limit=20.

## Analysis

For each error returned, assess:

1. **Is it new?** (first seen in this window vs recurring)

2. **What's the location?** Extract file path and function name from the stack trace.
   - **Beware over-grouped issues.** A single Sentry issue can span *multiple* endpoints/culprits (e.g. one `api.client_error` group covering face-mapping 409, batch-usage 400, stripe-connect 409 at once). Do NOT present one sample event's endpoint/status as THE issue. If the `title` is generic (e.g. `api.client_error.400`) or the `where`/culprit varies across events, label it as a **multi-endpoint group** and, if it matters, call `list_issue_events` / `get_issue_tag_values` to list the top culprits. Never fabricate a single endpoint+status from one sampled event.

3. **What's the severity?** Severity is a function of the Sentry `level` field FIRST, then the code path. **Event count is volume/trend signal only — it NEVER sets severity.** A high count of a handled rejection is usually bots/scanners/retries, not an emergency.

   **Step A — gate on `level` and error class:**
   - A `level: warning`, or any handled client error (title begins `api.client_error.`, or any 4xx status 400/401/403/404/409/422), is a request the server **correctly rejected** — it is NOT a crash or data loss. These are **MEDIUM at most, usually LOW**, regardless of which path they sit on. **Never mark a `level: warning` CRITICAL or HIGH**, even on a payment/booking/auth path.
   - Only `level: error` / `level: fatal` (real exceptions, 5xx, unhandled rejections) are eligible for HIGH or CRITICAL.

   **Step B — for genuine errors (level error/fatal), apply the code-path rubric:**
   - **CRITICAL**: payment processing, Stripe webhooks, booking/appointment creation, auth/login, onboarding wizard, subscription management, consent forms
   - **HIGH**: calendar rendering, client management, SMS sending, email sending, API middleware
   - **MEDIUM**: dashboard widgets, analytics, settings pages, content generation
   - **LOW**: cosmetic UI issues, non-blocking background jobs, dev-only paths

   If `level` is missing from the data, treat `api.client_error.*` / 4xx titles as warnings (Step A) and everything else by path (Step B).

4. **Is it user-facing?** Does it cause a visible crash, broken page, or lost data — vs a background error users never see?

5. **Frequency**: How many events? Trending up or stable?

## Output

Write findings to /Users/shane/Documents/Obsidian/Aestheticc/Ops/SENTRY_TRIAGE.md (replace content). Create the Ops/ directory if it doesn't exist.

```markdown
# Sentry Triage — [DATE TIME]

## Critical Path Errors (act now)
[errors in payment/booking/auth/onboarding/subscription flows]
[include: error message, file:line, event count, first/last seen]

## High Severity
[errors in calendar/client/SMS/email/API paths]

## New Errors (first seen this window)
[any error not seen before, regardless of path]

## Recurring
[known errors still firing — note if frequency is increasing]

## Summary
- Total errors: X
- Critical: X
- New: X
- Action needed: [yes/no + what]
```

If zero errors found, write:
```markdown
# Sentry Triage — [DATE TIME]
All clear. No errors in the last 6 hours.
```

If the MCP call fails, write the error to the file so Shane knows the check didn't run.
