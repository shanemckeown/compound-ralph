# What changes for you — AestheticcNext-yoi4o

## What you get back that you'd lost

Your morning Slack DM. Since 29 August you've had nothing at 07:00. You'll now get a DM
every morning at 07:00 listing the overnight results — which beads passed gates, which
need review, which got guarded — with PR links and preview links where they exist.

It works even on a night the batch ran normally. That was the whole bug: the 22:00 job
filed the results away into an archive folder, so by 07:00 the morning job looked in the
only place it knew about, found nothing, and quit silently. The morning job now also reads
yesterday's and today's archives, so it can't be starved that way again.

## What moves

**Your 22:00 Slack DM goes away. Everything it said now arrives at 07:00 instead.**

That's the one thing to be aware of. The 22:00 message was the "Overnight — N PRs" DM;
it had been going out with 0 PRs for weeks because the interesting content wasn't ready
yet at that hour. At 22:00 the batch now just writes SHANE_TODO.md and files the results;
it sends you nothing. The 07:00 DM carries the PR list, the dream pages, the zero-eligible
alert and the status counts.

If you'd rather still get a nudge at 22:00, say so — it's a small change to add back.

## What stays the same

- SHANE_TODO.md is still written at 22:00, same format, same place.
- The 07:30 "Overnight triage" pipeline-health DM still arrives at 07:30. It had also been
  dead — silently crashing every night for 22 days on a Bash error the first time it found
  a bead. That's fixed; it now reports properly and reads the archives too.
- No schedule changed. No launchd job was added, removed or re-timed.

## What you'll notice on the first morning after this lands

You get one morning DM covering the results already sitting in the archive. If the night
before this lands already sent you its 22:00 message, that night's results appear once more
at 07:00 — a single duplicate on the changeover day, then it settles.

If Slack is unreachable at 07:00, the message is kept and sent on the next run rather than
dropped, and SHANE_TODO.md is still written regardless.

## Anything promised but not wired?

No. Both DMs were dry-run rendered against a fake result set and the exact text is in the
bead comment and VALIDATION.md. What can't be proven from here is the real 07:00 launchd
firing — that only happens once this lands on main and the scripts are live. First real
proof is the morning after landing.
