# /safe-deploy — Activity-aware deploy gate

Replaces the calendar-based deploy embargo (Mon-Fri 09:00–19:30 BST = no prod) with a **real-signal** check: are users actually using the app right now, and are any clinics expecting it to work in the next few hours?

Calendar rule says "Tuesday 21:00 = green light" even when 4 clinics are mid-treatment. It says "Sunday 14:00 = red light" even when nobody is logged in and zero appointments are scheduled all day. Both are wrong. This skill checks the actual signal.

## Usage

```
/safe-deploy                        # Full check + deploy if SAFE (asks Shane first)
/safe-deploy --check                # Check only — don't deploy, just report
/safe-deploy --window               # 24h forward view: quiet vs busy slots
/safe-deploy --window 48h           # Custom forward window
/safe-deploy --force --reason "S1: Stripe webhook 500s"   # Bypass with audit log
```

## Primary path: run the committed script (don't re-inline queries)

The liveness check lives in the repo as a reusable script — **run it, don't re-type the queries below**:

```bash
cd /Users/shane/Documents/GitReBase/AestheticcNext
# Prereq: cloud-sql-proxy on :5432 (see "Prerequisite" below) — human-gated to start.
bun scripts/safe-deploy-check.mjs            # rendered verdict block (exit 0 SAFE/SAFE-NOW, 1 CAUTION, 2 BLOCKED/proxy-down)
bun scripts/safe-deploy-check.mjs --json     # raw signals for scripting
bun scripts/safe-deploy-check.mjs --window   # 24h forward view of quiet/busy slots
```

The inline SQL in "Implementation steps" below is the **source of truth the script encodes** + the fallback if the script is somehow missing. If you change a query, change it in `scripts/safe-deploy-check.mjs` and reflect it here. Added 2026-05-27 (PR #327) to stop re-inventing this every run.

> **Internal-clinic exclusion (2026-06-16).** The script filters out our own demo/QA clinics from every signal so they don't read as real activity (a seeded midnight demo appointment was showing as a busy hour in `--window`). Excluded by ID: `a4cc955f-7859-4dcc-bfb2-c0b28c0bba17` (Aestheticc Demo Clinic), `c2008c53-dca4-446b-9842-cb611c1f9f9d` (Claude QA Sandbox), plus a name net (`business_name NOT ILIKE '%demo clinic%' / '%qa sandbox%'`). The inline fallback SQL below omits this filter for brevity — if you ever run the raw queries, add `AND bp.id NOT IN ('a4cc955f-7859-4dcc-bfb2-c0b28c0bba17', 'c2008c53-dca4-446b-9842-cb611c1f9f9d')` to each.

## Signals checked (in order)

1. **Recent API activity** — anyone hitting the platform in the last 5 minutes (via `data_access_logs`, the canonical liveness signal — auth is JWT-based so the `sessions` table is effectively unused)
2. **Recent business events** — `activity_logs` entries (e.g. `client_created`, `pos_checkout_completed`) in the last 10 minutes as a secondary signal
3. **Active terminal readers / payments mid-flight** — anyone with a Stripe payment in `pending`, `processing`, `requires_action`, or `requires_payment_method`
4. **Upcoming appointments** — anything scheduled in the next 4 hours (default window)
5. **Consent forms recently signed** — `consent_forms.signed_at` in the last 10 minutes (signal that a clinic is mid-treatment intake)

## Verdict matrix

| State | Verdict | Action |
|---|---|---|
| 0 recent API activity, 0 readers active, 0 appointments in window | **SAFE** | Proceed with deploy after one-line Shane confirm |
| 0 recent activity, 0 readers, appointments later in window (>30 min away) | **SAFE-NOW** | Show next appointment time, recommend completing within window |
| Recent activity but no payment/appointment within 30 min | **CAUTION** | Show which clinic is active, ask Shane |
| Active terminal reader OR appointment in next 30 min OR consent form signed in last 10 min | **BLOCKED** | Refuse unless `--force --reason` |

## Implementation steps

> ⚠ **Time math: always do it in SQL** (`EXTRACT(EPOCH FROM (NOW() - col))::int AS seconds_ago`). Never trust bun's JS Date for naive `timestamp without time zone` columns — the bun postgres-js driver reads them as Europe/London local, converts to UTC, and emits a JS Date with a misleading `Z` suffix that is exactly 1 hour earlier than reality. All four relevant tables (`appointments`, `payments`, `activity_logs`, `data_access_logs`) use naive timestamps. Filter and bucket in the DB; only pass already-computed scalars (seconds_ago, BST display strings) into JS.

### Prerequisite: cloud-sql-proxy must be running

```bash
ps aux | grep cloud-sql-proxy
# If not running:
cloud-sql-proxy aestheticc:europe-west2:aestheticc-db --port=5432 &
```

The DB connection pattern below assumes the proxy is listening on `localhost:5432`.

### Step 1: Pre-flight

- If `--force` set: require `--reason` string of >20 chars; append entry to `~/.claude/safe-deploy-overrides.log` with timestamp + reason + git SHA. Then proceed straight to `@deploy`.
- Otherwise: continue.

### Step 2: Query recent API activity (liveness)

Try ops MCP first:
```
mcp__aestheticc-ops__get_business_activity
mcp__aestheticc-ops__get_terminal_reader_stats
```

If those don't return what we need, fall back to direct DB query. Fetch the secret directly from Secret Manager (the old `scripts/with-env.sh` wrapper has been removed):

```bash
cd /Users/shane/Documents/GitReBase/AestheticcNext
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL --project=aestheticc)
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const active = await sql`
  SELECT
    bp.business_name AS clinic,
    bp.id AS business_id,
    COUNT(*)::int AS hits,
    EXTRACT(EPOCH FROM (NOW() - MAX(d.created_at)))::int AS seconds_since_last_hit
  FROM data_access_logs d
  JOIN business_profiles bp ON bp.id = d.business_id
  WHERE NOW() - d.created_at < INTERVAL '\''5 minutes'\''
  GROUP BY bp.id, bp.business_name
  ORDER BY seconds_since_last_hit ASC
`;
console.log(JSON.stringify(active, null, 2));
await sql.end();
'
```

Secondary signal — meaningful business events in the last 10 minutes:

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const events = await sql`
  SELECT
    bp.business_name AS clinic,
    a.type,
    a.entity_type,
    a.description,
    EXTRACT(EPOCH FROM (NOW() - a.created_at))::int AS seconds_ago
  FROM activity_logs a
  JOIN business_profiles bp ON bp.id = a.business_id
  WHERE NOW() - a.created_at < INTERVAL '\''10 minutes'\''
    AND COALESCE(a.internal_only, false) = false
  ORDER BY a.created_at DESC
  LIMIT 50
`;
console.log(JSON.stringify(events, null, 2));
await sql.end();
'
```

### Step 3: Query upcoming appointments

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const appts = await sql`
  SELECT
    bp.business_name AS clinic,
    a.status,
    EXTRACT(EPOCH FROM (a.start_time - NOW()))::int AS seconds_until_start,
    to_char(a.start_time AT TIME ZONE '\''Europe/London'\'', '\''Dy YYYY-MM-DD HH24:MI'\'') AS start_bst
  FROM appointments a
  JOIN business_profiles bp ON bp.id = a.business_id
  WHERE a.start_time BETWEEN NOW() AND NOW() + INTERVAL '\''4 hours'\''
    AND a.status NOT IN ('\''cancelled'\'', '\''completed'\'', '\''no_show'\'')
  ORDER BY a.start_time
`;
console.log(JSON.stringify(appts, null, 2));
await sql.end();
'
```

### Step 4: Query in-flight payments (terminal readers + cards)

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const active = await sql`
  SELECT
    bp.business_name AS clinic,
    p.amount,
    p.status,
    EXTRACT(EPOCH FROM (NOW() - p.updated_at))::int AS seconds_since_update
  FROM payments p
  JOIN business_profiles bp ON bp.id = p.business_id
  WHERE p.status IN ('\''pending'\'', '\''processing'\'', '\''requires_action'\'', '\''requires_payment_method'\'')
    AND NOW() - p.updated_at < INTERVAL '\''10 minutes'\''
  ORDER BY p.updated_at DESC
`;
console.log(JSON.stringify(active, null, 2));
await sql.end();
'
```

### Step 5: Consent forms signed in last 10 minutes

`consent_forms` has no `status` or `updated_at` column — only `signed_at`, `created_at`, plus cooling-off fields. So we use `signed_at` as the proxy for "a clinic just signed someone in for treatment":

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const signed = await sql`
  SELECT
    bp.business_name AS clinic,
    EXTRACT(EPOCH FROM (NOW() - cf.signed_at))::int AS seconds_ago
  FROM consent_forms cf
  JOIN business_profiles bp ON bp.id = cf.business_id
  WHERE cf.signed_at IS NOT NULL
    AND NOW() - cf.signed_at < INTERVAL '\''10 minutes'\''
  ORDER BY cf.signed_at DESC
`;
console.log(JSON.stringify(signed, null, 2));
await sql.end();
'
```

### Step 6: Render verdict

Compute the BST timestamp in SQL too, so we don't fight the bun timezone quirk:

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const now = await sql`SELECT to_char(NOW() AT TIME ZONE '\''Europe/London'\'', '\''Dy YYYY-MM-DD HH24:MI'\'') AS bst`;
console.log(now[0].bst);
await sql.end();
'
```

Print a compact block:

```
⏱  Now: Sun 2026-05-10 14:00 BST

✓ 0 active clinics (no data_access_logs hits in last 5 min)
✓ 0 in-flight payments
✓ 0 appointments in next 4h
✓ 0 consent forms signed in last 10 min

VERDICT: SAFE  →  proceed with deploy?
```

Or for blocked:

```
⏱  Now: Tue 2026-05-12 21:30 BST

⚠ 1 in-flight payment (Omorphia, £180, processing, 2m since update)
⚠ 1 appointment in next 30 min (Awlin Beauty, Tue 2026-05-12 22:00, status confirmed)

VERDICT: BLOCKED  →  retry in 90 min, or /safe-deploy --force --reason "..."
```

### Step 7: Forward window mode (--window)

Bucket the next 24h (or N hours if specified) by hour using DB-side `date_trunc` so we don't fight the bun timezone quirk:

```bash
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const buckets = await sql`
  SELECT
    to_char(date_trunc('\''hour'\'', a.start_time) AT TIME ZONE '\''Europe/London'\'', '\''Dy YYYY-MM-DD HH24:MI'\'') AS hour_bst,
    COUNT(*)::int AS appts,
    COUNT(DISTINCT a.business_id)::int AS clinics
  FROM appointments a
  WHERE a.start_time BETWEEN NOW() AND NOW() + INTERVAL '\''24 hours'\''
    AND a.status NOT IN ('\''cancelled'\'', '\''completed'\'', '\''no_show'\'')
  GROUP BY date_trunc('\''hour'\'', a.start_time)
  ORDER BY date_trunc('\''hour'\'', a.start_time)
`;
console.log(JSON.stringify(buckets, null, 2));
await sql.end();
'
```

Then walk the buckets in code and mark quiet windows (≥3 consecutive hours of zero appointments) as 🟢 DEPLOY WINDOW.

Output:

```
24h forward view (Sun 14:00 → Mon 14:00 BST)

🟢 DEPLOY WINDOW  Sun 14:00–23:59  (0 appointments — wide open)
🟡 ACTIVE         Mon 09:00–11:59  (12 appointments, 5 clinics)
🔴 PEAK           Mon 12:00–14:00  (47 appointments, 14 clinics) ← do not deploy

Next safe window starts:  in 30 min (now)
Next safe window after morning peak:  Mon ~19:30 (resumes embargo logic)
```

### Step 8: If proceeding

After Shane's one-word confirm:
1. Hand off to `@deploy` agent (production) or `@deploy-staging` (staging) — whichever was implied by context
2. If neither was implied, ask: "Production or staging?"

## Override audit log

Every `--force` deploy appends to `~/.claude/safe-deploy-overrides.log`:

```
2026-05-10T14:23:00Z | SHA abc123f | PROD | reason: "S1: Stripe webhook returning 500"
```

Review weekly during `/retro` to spot pattern abuse ("hotfix" reasons that weren't really S1).

## Schema introspection (run once if SQL errors)

If any query above fails with "column does not exist", introspect the real shape:

```bash
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL --project=aestheticc)
bun -e '
import postgres from "postgres";
let url = process.env.DATABASE_URL;
url = url.replace(/\?host=.*$/, "");
url = url.replace(/@[^\/]*\//, "@localhost:5432/");
const sql = postgres(url);
const targets = ["data_access_logs", "activity_logs", "appointments", "payments", "business_profiles", "consent_forms"];
for (const t of targets) {
  const cols = await sql`
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = '\''public'\'' AND table_name = ${t}
    ORDER BY ordinal_position
  `;
  console.log(`\n== ${t} ==`);
  for (const c of cols) console.log(`  ${c.column_name.padEnd(30)} ${c.data_type}`);
}
await sql.end();
'
```

Known column lists at time of writing (2026-05-12):

- **data_access_logs**: `id, user_id, business_id, action, resource_type, resource_id, ip_address, user_agent, request_id, metadata, created_at`
- **activity_logs**: `id, user_id, business_id, type, entity_type, entity_id, description, metadata, created_at, internal_only`
- **appointments**: `start_time, end_time, status, business_id, updated_at` (+ others) — status enum excludes `cancelled, completed, no_show` for "still live"
- **payments**: `updated_at`, `status` (values `pending, processing, requires_action, requires_payment_method` mean in-flight), `amount`, `business_id`
- **business_profiles**: `id`, `business_name` (NOT `name`), …
- **consent_forms**: `signed_at`, `created_at`, cooling-off fields — no `status`, no `updated_at`

Update the queries above in this file if introspection reveals drift. The skill is meant to evolve.

## Why this skill exists

The 19:30 BST embargo was set when we had no signal. Now we have the database — the rule should be "is the platform actually idle" not "what does the clock say". Sunday afternoon is the most under-used deploy window in the schedule and we're leaving it on the table.

Created 2026-05-10 in response to: "make a /safe-deploy that doesn't deploy when someone's using aestheticc, and shows a forward view of safe slots". Patched 2026-05-12 to match real prod schema (`business_profiles.business_name`, `data_access_logs` for liveness instead of dead `sessions` table, DB-side time math to dodge the bun postgres-js naive-timestamp quirk, and direct `gcloud secrets` access in place of the removed `scripts/with-env.sh` wrapper).
