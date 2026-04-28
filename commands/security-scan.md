# /security-scan - AI-Powered Security Vulnerability Scanner

Automated security scanner that uses AI reasoning (not just pattern matching) to find vulnerabilities in the Aestheticc codebase. Inspired by Claude Code Security (Team/Enterprise only) — this is our MVP.

## Usage
```
/security-scan                    # Full scan (all 5 domains, ~30min)
/security-scan auth               # Single domain scan
/security-scan scope=pages/api/payments  # Targeted directory scan
/security-scan gate               # Pre-deploy gate (changed files only)
/security-scan delta              # Files changed since last scan
```

## Architecture

Three-stage pipeline: Discovery -> Verification -> Output

```
STAGE 1: Discovery (5 parallel subagents)
  |-- Agent A: Auth & Access Control
  |-- Agent B: Injection Vectors
  |-- Agent C: Data Flow & PII Tracing
  |-- Agent D: Business Logic & Payment Integrity
  |-- Agent E: Secrets, Config & Infrastructure
       |
STAGE 2: Verification (disprove each finding)
       |
STAGE 3: Output (findings + patches -> QA system)
```

## When Invoked

### Step 0: Setup

Read the current QA state to understand what's already known:

1. Read `AestheticcNext/Product/QA/QA_FINDINGS.md` — get the highest QA-NNN ID to continue the sequence
2. Read `AestheticcNext/Product/QA/QA_METHODOLOGY.md` (first 80 lines) — refresh on severity scale
3. Read `AestheticcNext/Product/QA/QA_DASHBOARD.md` — understand current layer scores

Parse the scan mode from arguments:
- No args or `full` → full scan, all 5 discovery agents
- A domain name (e.g., `auth`, `injection`, `data-flow`, `payments`, `infra`) → single agent
- `scope=<path>` → all agents but scoped to that directory
- `gate` → scan only files changed since last commit on main
- `delta` → scan files changed since last scan (check `AestheticcNext/Product/QA/LAST_SCAN.json`)

Determine scope:
- For `gate`: `git diff --name-only HEAD~1` to get changed files
- For `delta`: Read `LAST_SCAN.json` for last scan commit hash, then `git diff --name-only <hash>..HEAD`
- For `scope=<path>`: Only scan files under that path
- For `full`: Scan `pages/api/`, `lib/`, `components/`, `middleware.ts`

### Step 1: Discovery (Parallel Subagents)

Launch 5 Task subagents in parallel using `subagent_type=Explore`. Each agent gets:
- A specific security domain to focus on
- The list of files in scope
- Instructions to output structured findings

**IMPORTANT:** Each agent should REASON about the code, not just grep for patterns. Read the actual logic, trace data flows, understand middleware chains.

#### Agent A: Auth & Access Control

```
Scan the following files for authentication and authorization vulnerabilities.

CODEBASE: /Users/shane/Documents/GitReBase/AestheticcNext
SCOPE: [files in scope]

CHECK FOR:
1. API routes missing auth middleware (should have withAuth, withBusinessStudioAuth, withPortalAuth, or withLegacyAuth)
   - Read each pages/api/ file's export default — what middleware wraps the handler?
   - Public endpoints MUST be explicitly documented (webhooks, portal access, payment checkout)
2. Broken tenant isolation
   - Every DB query that reads/writes business data MUST include a businessId WHERE clause
   - Check: does the businessId come from the authenticated session, not from the request body?
   - Trace: req.user.id -> getUserBusinessId() -> query WHERE businessId=X
3. Privilege escalation
   - Can a regular user access admin-only routes?
   - Can a portal user (client) access dashboard routes?
   - Can one business's team member see another business's data?
4. Session/token handling
   - Are JWTs validated properly? Checked for expiry?
   - Are magic link tokens single-use? Expired after use?
   - Are refresh tokens rotated?
5. CORS configuration
   - Is Access-Control-Allow-Origin set to * anywhere?
   - Are credentials sent cross-origin?

OUTPUT FORMAT (one per finding):
FINDING: [short title]
FILE: [path:line]
SEVERITY: S1|S2|S3|S4|S5
DESCRIPTION: [what the vulnerability is]
ATTACK_PATH: [how an attacker could exploit this]
DATA_AT_RISK: [what data could be accessed/modified]
SUGGESTED_FIX: [code diff or description]
```

#### Agent B: Injection Vectors

```
Scan the following files for injection vulnerabilities.

CODEBASE: /Users/shane/Documents/GitReBase/AestheticcNext
SCOPE: [files in scope]

CHECK FOR:
1. SQL Injection
   - Any use of sql.raw(), sql`...${userInput}...` with unescaped user input
   - String concatenation in SQL queries
   - Drizzle ORM is safe by default, but raw SQL escapes it
2. XSS (Cross-Site Scripting)
   - User content rendered without sanitization in React components
   - dangerouslySetInnerHTML with user data
   - ReactMarkdown without rehypeSanitize
   - Server-rendered HTML templates with user data (email templates, TwiML)
3. Command Injection
   - child_process.exec/spawn with user input
   - Any shell command construction with template strings
4. Template Injection
   - TwiML responses with unescaped user data (phone numbers, names)
   - Email HTML templates with unescaped user data
   - SMS templates with user data
5. Path Traversal
   - File operations (fs.readFile, etc.) with user-controlled paths
   - Image upload/download paths constructed from user input
6. ReDoS
   - new RegExp() with user-supplied patterns
   - Check: is the input escaped with a function like escapeRegExp()?
7. Server-Side Request Forgery (SSRF)
   - fetch()/axios with user-controlled URLs
   - Webhook URL callbacks with user-supplied URLs

OUTPUT FORMAT: Same as Agent A
```

#### Agent C: Data Flow & PII Tracing

```
Trace how PII moves through the Aestheticc application. This is the most important scan.

CODEBASE: /Users/shane/Documents/GitReBase/AestheticcNext
SCOPE: [files in scope]

Aestheticc stores these PII types:
- Client names, emails, phone numbers (clients table)
- Medical notes, treatment records (clientNotes, appointments tables)
- Before/after photos — potentially intimate medical images (photoSessions table, GCS storage)
- Payment data — card tokens via Stripe, bank details via GoCardless (payments, gocardlessMandates tables)
- NHS numbers (prescriptions table)
- Digital signatures on consent forms (formResponses table)
- Facial mapping coordinates (facialMappings table)
- Client addresses (clients table)

FOR EACH PII TYPE, TRACE:
1. INPUT: How does it enter the system?
   - Which API endpoints accept it? Is it validated (Zod schema)?
   - Is it sanitized (HTML stripped, trimmed)?
2. PROCESSING: What happens to it?
   - Is it transformed, normalized, or enriched?
   - Is it ever passed to third-party services (Stripe, Twilio, Resend, Sentry)?
3. STORAGE: How is it stored?
   - Is it encrypted at rest? (NHS numbers SHOULD be)
   - Are there access controls on the DB queries?
   - Is the data ever cached (Redis, in-memory)?
4. OUTPUT: Where does it leave the system?
   - API responses — is PII included unnecessarily?
   - Logs — does Logger.info/error include PII?
   - Error reporting — does Sentry receive PII in breadcrumbs/contexts?
   - Emails/SMS — is PII in message templates?
   - Analytics — does PostHog receive PII?
5. LEAKAGE: Does PII appear where it shouldn't?
   - In URL query parameters or path segments
   - In client-side JavaScript bundles
   - In git history (hardcoded test data)
   - In error messages returned to the client

SPECIFIC CHECKS:
- Medical photos: Are GCS URLs signed with expiry? Or permanent public URLs?
- Consent signatures: Can they be accessed without proper auth?
- NHS numbers: Are they encrypted at rest and decrypted only when needed?
- Client search: Does the search endpoint return full PII or minimal results?
- Data export: Does GDPR export include all PII correctly?
- Data deletion: Does "delete client" cascade to all PII tables?

OUTPUT FORMAT: Same as Agent A, but also include:
PII_TYPE: [which PII type is affected]
DATA_FLOW: [input -> processing -> storage -> output path]
```

#### Agent D: Business Logic & Payment Integrity

```
Scan for business logic flaws and payment integrity issues.

CODEBASE: /Users/shane/Documents/GitReBase/AestheticcNext
SCOPE: [files in scope]

CHECK FOR:
1. Payment Amount Manipulation
   - Can a client modify the payment amount via the API?
   - Is the amount re-calculated server-side or trusted from the client?
   - Are currency conversions/rounding done correctly?
   - Is application_fee_amount always passed for Stripe Connect charges?
2. State Machine Violations
   - Appointment status: can it go backwards (completed -> scheduled)?
   - Payment status: can a "succeeded" payment be modified?
   - Subscription lifecycle: trial -> active -> past_due -> cancelled — are transitions enforced?
   - Consent forms: can a signed form be modified after signing?
3. Race Conditions
   - Double-booking: can two requests book the same time slot?
   - Double-payment: can a payment link be used twice?
   - Credit/package balance: can concurrent requests overdraw the balance?
   - Check for DB transactions around read-modify-write sequences
4. Package/Membership Abuse
   - Can a client use more units than purchased?
   - Can packages be applied to the wrong business?
   - Are expiry dates enforced server-side?
5. Idempotency
   - Do payment creation endpoints have idempotency keys?
   - Can a webhook be replayed to duplicate an action?
   - Are Stripe webhook events checked for duplicates?
6. Fee Calculation
   - Platform fee (application_fee_amount) = 2% of charge amount
   - Is this consistently calculated for all payment types?
   - Terminal (direct charges), Online (destination charges), GoCardless — all 2%?

OUTPUT FORMAT: Same as Agent A
```

#### Agent E: Secrets, Config & Infrastructure

```
Scan for secrets exposure, configuration issues, and infrastructure vulnerabilities.

CODEBASE: /Users/shane/Documents/GitReBase/AestheticcNext
SCOPE: [files in scope]

CHECK FOR:
1. Hardcoded Secrets
   - API keys, tokens, passwords in source code
   - Test credentials that work in production
   - Fallback/default values for secrets (e.g., "default-secret")
2. Environment Variable Exposure
   - Server-side env vars leaked in API responses
   - NEXT_PUBLIC_ vars that shouldn't be public
   - Env vars in error messages or logs
3. Rate Limiting
   - Sensitive endpoints without rate limiting (login, magic link request, payment creation)
   - Check for RateLimitPresets usage on auth endpoints
4. Missing Security Headers
   - CORS overly permissive
   - No CSP header
   - No X-Frame-Options
5. Dependency Vulnerabilities
   - Check npm audit output
   - Known vulnerable packages
6. Docker/Deployment
   - Secrets in Dockerfile build args (DATABASE_URL is there!)
   - Container running as root?
   - Debug endpoints still deployed?
7. Webhook Security
   - Are incoming webhooks verified (Stripe signature, GoCardless HMAC)?
   - Can webhook endpoints be called by anyone?
8. Logging
   - Are secrets ever logged?
   - Is PII logged at INFO level? (Should be DEBUG at most)

OUTPUT FORMAT: Same as Agent A
```

### Step 2: Verification

After all discovery agents complete, collect all findings. For EACH finding:

1. **Read the actual file** at the specified path:line
2. **Check for upstream guards:**
   - Is there middleware that handles this? (e.g., `withAuth` wrapping the handler)
   - Is there validation earlier in the request chain?
   - Is the file dead code / feature-flagged off / unreachable?
3. **Attempt to disprove:**
   - Can you construct a concrete attack path? If not, downgrade to LOW.
   - Is the "vulnerability" actually by design? (e.g., public endpoints)
   - Would existing middleware/auth prevent exploitation?
4. **Assign confidence:**
   - **HIGH** — Concrete exploit path exists. Include proof-of-concept description.
   - **MEDIUM** — Likely real but requires specific conditions (race under load, insider access).
   - **LOW** — Theoretical, requires unrealistic conditions, or already mitigated. Log but drop from report.
5. **Check for duplicates:**
   - Read QA_FINDINGS.md — does this finding already exist?
   - If yes and it's OPEN, note the existing ID. Don't create a duplicate.
   - If yes and it's FIXED, verify the fix is still in place.

Drop all LOW confidence findings. Keep only HIGH and MEDIUM.

### Step 3: Output

#### 3a. Write scan report

Create `AestheticcNext/Product/QA/SECURITY_SCAN_[YYYY-MM-DD].md`:

```markdown
# Security Scan — [YYYY-MM-DD]

**Mode:** [full|targeted|gate|delta]
**Scope:** [files scanned count]
**Duration:** [time taken]
**Agents:** [which discovery agents ran]

## Summary

| Severity | New Findings | Existing (Rediscovered) | False Positives Filtered |
|----------|-------------|------------------------|-------------------------|
| S1 | X | X | X |
| S2 | X | X | X |
| S3 | X | X | X |

## New Findings

### QA-NNN: [Title]
- **Severity:** S1-S5
- **Confidence:** HIGH / MEDIUM
- **Layer:** 1-10
- **File:** path/to/file.ts:line
- **Description:** What the vulnerability is
- **Attack path:** How it could be exploited
- **Data at risk:** What PII/data is exposed
- **Suggested fix:**
  ```diff
  - vulnerable code
  + fixed code
  ```
- **Verification:** Why this is not a false positive

[...repeat for each finding...]

## Rediscovered (Already Known)
- QA-XXX: [title] — still open, confirmed
- QA-YYY: [title] — marked fixed, verified fix is in place

## Scan Metadata
- Files scanned: [count]
- Discovery agents: [which ones]
- Findings before verification: [count]
- Findings after verification: [count]
- False positive rate: [percentage filtered]
```

#### 3b. Update QA_FINDINGS.md

For each NEW finding (not already in QA_FINDINGS.md):
1. Assign the next QA-NNN ID
2. Append to the appropriate section in QA_FINDINGS.md
3. Set status to OPEN

#### 3c. Update scan state

Write `AestheticcNext/Product/QA/LAST_SCAN.json`:
```json
{
  "date": "YYYY-MM-DD",
  "commit": "[git rev-parse HEAD]",
  "mode": "full",
  "files_scanned": 150,
  "findings_before_verification": 25,
  "findings_after_verification": 12,
  "new_findings": 8,
  "rediscovered": 4,
  "false_positive_rate": 0.52
}
```

#### 3d. Update QA_DASHBOARD.md

Add a row to the audit pass history table:
```
| Security Scan | [date] | 1-10 | Automated scanner v1 | [findings count] | 0 |
```

#### 3e. Log in QA_PASSES.md

Append a pass entry with the scan details.

### Step 4: Report

Print a concise summary to the user:

```
Security Scan Complete

Mode: full | Files scanned: 150 | Duration: 28 min

  S1 Critical:  2 new, 1 rediscovered
  S2 High:      5 new, 3 rediscovered
  S3 Medium:    3 new

Highest priority:
  QA-245 [S1] NHS numbers logged to Sentry breadcrumbs (pages/api/prescriptions/create.ts:89)
  QA-246 [S1] Photo URLs not signed — permanent public access (lib/utils/storage.ts:51)

Report: AestheticcNext/Product/QA/SECURITY_SCAN_2026-02-20.md
Updated: QA_FINDINGS.md (+8 findings), QA_DASHBOARD.md, QA_PASSES.md
```

## Domain-to-Agent Mapping

When a single domain is specified:

| Argument | Agent |
|----------|-------|
| `auth` | Agent A: Auth & Access Control |
| `injection` | Agent B: Injection Vectors |
| `data-flow` or `pii` | Agent C: Data Flow & PII Tracing |
| `payments` or `business-logic` | Agent D: Business Logic & Payment Integrity |
| `infra` or `secrets` or `config` | Agent E: Secrets, Config & Infrastructure |

## Token Budget

- Full scan: ~5 Explore agents + 1 verification pass = ~1 conversation worth
- Targeted scan: ~1-2 agents = lighter
- Gate scan: Usually <20 files = very light

## Related

- **QA System:** `AestheticcNext/Product/QA/` (methodology, findings, dashboard, passes)
- **Plan:** `Aestheticc/Product/SECURITY_SCAN_PLAN.md`
- **Module audit:** `/audit` (complementary — module-by-module review)
- **Inspiration:** Claude Code Security (Anthropic, Team/Enterprise only)
