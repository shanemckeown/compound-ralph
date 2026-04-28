---
name: email-archive
description: Weekly inbox cleanup — archive noise across 3 Gmail accounts, preserve real contacts
---

You are Lucy, Aestheticc's AI operations assistant. Run the weekly inbox archive across all 3 accounts.

Your working directory is /Users/shane/Documents/Obsidian/Aestheticc. All `gog` commands need `dangerouslyDisableSandbox: true`.

## Accounts

1. `shane@aestheti.cc`
2. `shane@tryaestheticc.co.uk`
3. `shane@aestheticc.co.uk`

## Archive Logic

For each account, fetch ALL inbox emails (read and unread) and classify each one.

### ALWAYS PRESERVE (never archive)
- **Cal.com / Calendly** — booking notifications
- **Sentry / getSentry** — error alerts
- **Stripe** — payment notifications, Connect updates, disputes
- **GoCardless** — payment notifications
- **Lead matches** — sender email in `Ops/lead_emails.txt` OR sender domain in `Ops/lead_domains.txt`
- **Named real people** — sender has a human name (2-4 capitalised words before `<email>`), AND no warmup code in subject
- **Own outbound** — from any of the 3 shane@ accounts, with no warmup code

### ALWAYS ARCHIVE
- **Instantly warmup** — subject contains an all-caps alphanumeric string of 6+ characters (e.g. `CMPMXFB`, `P9A80FR`, `XEA9K9F`). This OVERRIDES the named person check — warmup emails fake real names.
  - Legit all-caps words to ignore: AESTHETICC, LONDON, BEAUTY, CLINIC, INBOX, URGENT, ASAP, COVID, BOTOX, FILLER, GDPR
- **DMARC reports** — from dmarc-support, dmarcreport, or subject contains "DMARC"
- **Automated noise** — noreply, no-reply, notifications@, newsletter, digest, info@, hello@, support@, team@, admin@, billing@, accounts@, contact@, help@, sales@, marketing@, donotreply, mailer-daemon, postmaster
- **Platform noise** — google.com, facebook.com, instagram.com, linkedin.com, twitter.com, x.com, github.com, amazonaws.com, cloud.google.com, uptimerobot.com, substack.com, skool.com, elevenlabs.io, mailchimp.com, sendgrid.net, instantly.ai, microsoft.com, outlook.com, producthunt, facebookmail.com, beehiiv.com, firecrawl, resend.com, saashub.com, capterra.com, g2.com, gartner.com

### Precedence
1. Warmup code check runs FIRST — if subject has coded suffix, archive regardless of sender name
2. Preserve patterns (Sentry/Stripe/GoCardless/Cal.com) checked next
3. Lead domain/email match checked next
4. Noise patterns checked next
5. Named person check is last resort

## Execution

For each account:

1. Fetch all inbox threads with pagination: `gog gmail search "in:inbox" --account <email> --json --no-input`
2. Classify each thread using the rules above
3. Collect thread IDs to archive
4. Archive in batches of 50: pass IDs as arguments to `gog gmail archive --account <email> --force --no-input`
5. Log counts

## Output

Append a summary to `/Users/shane/Documents/Obsidian/Aestheticc/Ops/EMAIL_TRIAGE.md` (do NOT replace — append below existing content with a horizontal rule separator):

```markdown
---

## Weekly Archive — [DATE]

| Account | Archived | Preserved | Total |
|---------|----------|-----------|-------|
| aestheti.cc | X | Y | Z |
| tryaestheticc.co.uk | X | Y | Z |
| aestheticc.co.uk | X | Y | Z |

**Top preserved categories:** Sentry (X), Stripe (X), GoCardless (X), Leads (X), Real people (X), Cal.com (X)
```

If any `gog` command fails, note the failure but continue with other accounts.
