# Review-lead dismissal patterns

Learned patterns the review lead (`LEAD.md`) may cite when it dismisses a reviewer finding. The format is cherry-picked from pstack's `bugbot-triage.md` (github.com/cursor/plugins, `pstack/skills/poteto-mode/references/bugbot-triage.md`).

**How the lead uses this file.** A dismissal may cite one pattern by its exact `###` heading, in the lead JSON's `pattern` field. `review_lead_check.py` refuses a pattern name that isn't a heading here. It also refuses any dismissal, with or without a pattern, of a finding that touches the never-dismiss areas: booking slots, money, sends and crons, webhooks, tenancy and auth, GDPR. Those are the "Do not skip when" boundaries every pattern shares, so they're enforced in code as well as written here.

**How the lead appends.** Record a new pattern only after it has dismissed the same shape of finding for a real reason. Start it at `Confidence: candidate`, and put the bead ID and round in `Source`. Promote it to `recurring` when a second bead confirms it. Promote to `strong` only when it's narrow and Shane has agreed. Never add a pattern whose "Skip when" covers money, booking slots, tenancy or patient-data exposure.

## Patterns

### multi-device concurrent edit of one clinical record

- Confidence: recurring
- Skip when: the failure needs two devices, tabs or users writing the SAME clinical record (face map, treatment note, consent, photo annotation) at the same time. Examples: a version conflict, a 409, a merge with "the other device's point", or two overlapping saves of one record. It also covers a symptom that looks single-device (e.g. a wrong-photo write) but can only *start* from a two-device conflict callback (tiy3q r3 F5). In a clinic, one practitioner writes one client's record on one device (CLINIC_REALITY). Phone to iPad is sequential, so it only needs the record to be current on open.
- Do not skip when: booking slots or availability, payments or the terminal, reminders, crons or sends, webhooks. Also not when the same failure happens on ONE device, e.g. switching record or photo mid-save and writing to the wrong one, a save that silently doesn't persist, or a stale map shown on open after a phone-to-iPad switch. Those are Act on.
- Example signal: "another device's point", "both devices change a dose", "two overlapping checked saves", "409 retried unchecked", "conflict refresh", "merges the remote point".
- Source: AestheticcNext-tiy3q round 3 (24 Sep 2026). Shane, 26 Sep: *"we never need two device sync ever"*. `feedback_clinic_reality_filter_on_findings`.

### a person acting inside a sub-second or few-second window

- Confidence: recurring
- Skip when: the failure needs a *human* to act inside a sub-second or few-second window. Examples: consent revoked in the milliseconds during a publish, drawing before the opening load returns, tapping two controls "at once". Also a millisecond gap between a working check and the action it guards. Shane, 26 Sep: *"mistaking or putting at high risk something that happens within just a few seconds or less than one second and thinking that humans might be doing something back and forth within that very short period of time."*
- Do not skip when: a **system** actor supplies or stretches the timing (webhook, retry or backoff queue, cron, network replay, double-click or double-submit). Also not when the window is realistically several seconds on clinic wifi (then the fix is the smallest guard, e.g. disable the pen until loaded), or when the check is missing or wrong for a normal user. Never on money, booking slots, sends or tenancy.
- Example signal: "before the opening GET returns", "between the check and", "if consent is revoked during", "race window", "within the same tick".
- Source: `feedback_consent_timing_races_not_real_bugs` (7kv3w.57, July 2026); CLINIC_REALITY core rule (Shane, 26 Sep 2026); tiy3q r3 F2.

### hand-crafted request the app never sends, with a trivial out-of-band equivalent

- Confidence: recurring
- Skip when: the gap needs a hand-built API request the real UI never sends, AND the same outcome is trivially possible out of band (screenshot, download and re-upload). "We are not the police."
- Do not skip when: cross-tenant access, auth bypass, unauthenticated access to patient data, payment correctness, GDPR deletion, clinical-safety gates on the prescribing pathway.
- Example signal: "a crafted request could", "an attacker could POST", "provenance mismatch".
- Source: `feedback_consent_timing_races_not_real_bugs` (7kv3w.45/.47/.48/.49/.62).
