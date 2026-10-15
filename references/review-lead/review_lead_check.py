#!/usr/bin/env python3
"""review_lead_check.py: the deterministic gate on the review lead's output (LUCY-oawgi).

The lead (LEAD.md) is a model; this is the part that must hold without Shane. It checks the
lead's JSON against the reviewer verdicts, then writes the ONLY file the builder reads.

  review_lead_check.py --lead lead-round-N.json --verdict astra-verdict-round-N.log \
      [--verdict opus-h2h-round-N.md] --out-dir "$EVIDENCE_DIR" --round N [--scope-lock]

Refuses (exit 1, reasons on stderr) when:
  - a reviewer finding line is missing from the lead, duplicated, or invented;
  - a verdict file is empty, truncated, or has a finding line in an unrecognised format;
  - a verdict is not act_on / consider / noted / dismissed;
  - a non-act_on item has no reason, or an act_on item has no clinic scenario / demonstration;
  - a finding in a never-dismiss area (booking slots, money, sends/crons, webhooks, tenancy/auth,
    GDPR) is Dismissed or Noted;
  - a cited pattern is not a heading in DISMISSAL_PATTERNS.md;
  - scope lock is on and an act_on item lacks in_scope, or an out-of-scope / consider item lacks
    a follow_up title;
  - more than 5 blocking act_on items without act_on_over_cap_reason;
  - round >= 2 and no premise block;
  - a pre_existing act_on / consider item lacks a follow_up bead title.
Duplicate path, H-D0 (LUCY-fuu2h, Shane 15 Oct 2026: "Yes, P2 blocks"):
  - a finding with category `duplicate_path`, or `H-D0` / "duplicate path" in its line, is graded P2
    whatever severity the reviewer wrote, so it can never be Noted;
  - it is structural: an act_on needs no clinic_scenario, and it is never demoted to a P4 note;
  - one the branch added that is Consider, or Act on but out of scope under the lock, escalates
    (blocks landing) instead of becoming a follow-up; a pre_existing one is a follow-up.
Ordinary-use triage (LUCY-r1lyy, Shane 7 Oct 2026, CLINIC_REALITY "The ordinary-use test"):
  - every finding states `ordinary_use` (how ONE person using the app normally triggers it with
    one or two ordinary actions, or "none: <why>") and `carve_out` (true when it touches money,
    patient data, auth, cross-tenancy, or what a user sees or is told);
  - an act_on / consider finding with no ordinary-use path that is not in the carve-out is
    auto-demoted to a P4 note: no fix, no follow-up bead, no re-review;
  - a carve-out finding (lead says so, protected category, user_facing category, or a protected
    keyword) is never demoted, however rare;
  - a `pre_existing: true` finding never blocks the branch under review: it goes to a filed
    follow-up bead (or to escalate when protected), with or without scope lock.
On success (exit 0) writes, into --out-dir:
  lead-actions-round-N.txt     the blocking Act-on findings: the builder's whole input
  lead-escalate-round-N.txt    protected-area Consider items: orchestrator decides, blocks push
  lead-followups-round-N.txt   follow-up bead titles (non-blocking)
  lead-dismissals-round-N.md   every non-Act-on decision with its reason (the audit trail)
and prints `BLOCKING=n ESCALATE=n FOLLOWUPS=n DISMISSED=n NOTED=n P4=n`.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATTERNS = HERE / "DISMISSAL_PATTERNS.md"
VERDICTS = {"act_on", "consider", "noted", "dismissed"}
ACT_ON_CAP = 5
# The lead puts every finding in one category. Protected categories can never be Dismissed or
# Noted; the keyword regex below is a second, independent backstop in case the lead mislabels.
PROTECTED_CATEGORIES = {"money", "booking", "sends", "webhooks", "tenancy_auth", "patient_data_exposure",
                        "gdpr", "prescribing", "single_device_data_loss"}
OPEN_CATEGORIES = {"clinical_record_concurrency", "timing_window", "crafted_request", "style", "other",
                   # what a user sees or is told (broken promise, wrong screen): carve-out from the
                   # ordinary-use demotion, but the lead may still dismiss a hypothetical one.
                   "user_facing"}
# H-D0: a second implementation of a job an existing table/module/setting/send path already does.
# Not protected (the lead may dismiss it with a reason, e.g. the plan recorded why extending failed).
STRUCTURAL_CATEGORIES = {"duplicate_path"}
CATEGORIES = PROTECTED_CATEGORIES | OPEN_CATEGORIES | STRUCTURAL_CATEGORIES
CARVE_OUT_CATEGORIES = PROTECTED_CATEGORIES | {"user_facing"} | STRUCTURAL_CATEGORIES
DUPLICATE_PATH = re.compile(r"\bH-D0\b|\bduplicate[- ]path\b", re.I)
# An ordinary_use value that says there is no ordinary path counts as absent.
NO_ORDINARY_USE = re.compile(r"^\s*(none|n/?a|no\b|not (an )?ordinary|unknown|-+)\b", re.I)

# Findings in these areas are real concurrency / real harm in a clinic (CLINIC_REALITY
# "never dismiss"). Narrow on purpose: words like "delete" or "authorize" alone are not here,
# because ordinary record edits use them.
PROTECTED = re.compile(
    r"\b(book(ing|ings|ed)?|slots?|availability|double[- ]book\w*|diary|waitlist"
    r"|pay(ment|ments|ing)?|charg(e|es|ed|ing)|refund\w*|stripe|terminal|pos\b|invoice\w*|deposit\w*|payout\w*"
    r"|reminder\w*|cron\w*|sms|webhook\w*|twilio|send(s|ing)? (an? )?(email|sms|message)"
    r"|debit\w*|credit(s|ed)?|balance|ledger|package (credit|session)s?"
    r"|tenan\w*|business_?id|businessId|cross[- ]clinic|another clinic|unauthori[sz]ed|unauthenticated"
    r"|authz|authori[sz]ation|auth(entication)? bypass|impersonat\w*"
    r"|gdpr|erasure|prescri\w*|contraindicat\w*"
    r"|H-M\d|H-B\d|H-C\d|H-T\d)\b",
    re.I,
)
FINDING = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(P[123])(?:\*\*)?[\s:]+\S+.*$")
SEVERITY_TOKEN = re.compile(r"\bP[123]\b")


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def clean(line):
    """Canonical form of a finding line: bullets and **bold** severity stripped."""
    return norm(re.sub(r"^(?:\*\*)?(P[123])(?:\*\*)?[\s:]+", r"\1 ", norm(line).lstrip("-* ")))


def reviewer_findings(paths):
    """Finding lines from each verdict file. Raises ValueError on anything that could hide a
    finding: no PASS/FAIL line, FAIL with nothing parsed, or a severity token we can't parse."""
    found = []
    for p in paths:
        text = Path(p).read_text(errors="ignore")
        heads = list(re.finditer(r"(?m)^\s*(?:\*\*)?(PASS|FAIL)(?:\*\*)?\s*$", text))
        if not heads:
            raise ValueError(f"{p}: no PASS/FAIL verdict line (empty or truncated review?)")
        # codex logs repeat the final message; keep the last verdict block only, up to its trailer.
        last = heads[-1]
        body = text[last.end():]
        stop = re.search(r"(?m)^\s*(?:#+\s*)?(HAZARDS|VERIFIED OK|UNVERIFIED)\b", body)
        mine = []
        for line in (body[:stop.start()] if stop else body).splitlines():
            if FINDING.match(line):
                mine.append(clean(line))
            elif SEVERITY_TOKEN.search(line):
                raise ValueError(f"{p}: unrecognised finding format: {line.strip()[:100]}")
        if last.group(1) == "FAIL" and not mine:
            raise ValueError(f"{p}: verdict is FAIL but no finding lines parsed")
        found += mine
    return found


def pattern_names():
    return {norm(m.group(1)).lower() for m in re.finditer(r"(?m)^###\s+(.+)$", PATTERNS.read_text())}


def is_duplicate_path(f, src):
    return f.get("category") in STRUCTURAL_CATEGORIES or bool(DUPLICATE_PATH.search(src))


def has_ordinary_use(f):
    text = norm(f.get("ordinary_use") or "")
    return len(text) >= 12 and not NO_ORDINARY_USE.match(text)


def is_carve_out(f, protected):
    return protected or f.get("carve_out") is True or f.get("category") in CARVE_OUT_CATEGORIES


def triage_ordinary_use(f, protected):
    """Mark an act_on / consider finding `_demoted` (P4 note) when it has no ordinary-use path
    and is not in the carve-out. Dismissed / noted items are already non-blocking."""
    f["_carve_out"] = is_carve_out(f, protected) or f.get("_duplicate_path") is True
    f["_demoted"] = (f.get("verdict") in ("act_on", "consider") and not f["_carve_out"]
                     and not has_ordinary_use(f))
    return f["_demoted"]


def routed_out(f, scope_lock):
    """An act_on item that leaves this branch: out of scope under the lock, or pre-existing."""
    return f.get("verdict") == "act_on" and not f.get("_demoted") and (
        f.get("pre_existing") is True or (scope_lock and f.get("in_scope") is False))


def check(lead, reviewer_lines, round_no, scope_lock):
    errs = []
    items = lead.get("findings")
    if not isinstance(items, list):
        return ["lead JSON has no findings list"], None
    known = pattern_names()
    seen = {}
    for i, f in enumerate(items):
        tag = f.get("id") or f"#{i + 1}"
        src = clean(f.get("source_line", ""))
        if not src:
            errs.append(f"{tag}: no source_line")
            continue
        seen[src] = seen.get(src, 0) + 1
        v = f.get("verdict")
        if v not in VERDICTS:
            errs.append(f"{tag}: verdict {v!r} is not one of {sorted(VERDICTS)}")
            continue
        cat = f.get("category")
        if cat not in CATEGORIES:
            errs.append(f"{tag}: category {cat!r} is not one of {sorted(CATEGORIES)}")
        protected = cat in PROTECTED_CATEGORIES or bool(PROTECTED.search(src))
        f["_protected"] = protected
        dup = f["_duplicate_path"] = is_duplicate_path(f, src)
        if triage_ordinary_use(f, protected):
            pass  # P4 note: no scenario, demonstration, reason or follow-up demanded
        elif v == "act_on":
            if not dup and not norm(f.get("clinic_scenario", "")):
                errs.append(f"{tag}: act_on needs a clinic_scenario (who, which device, what moment)")
            if not norm(f.get("demonstrated", "")):
                errs.append(f"{tag}: act_on needs 'demonstrated' (red test, repro or traced path)")
            if scope_lock:
                if not isinstance(f.get("in_scope"), bool):
                    errs.append(f"{tag}: scope lock is on, act_on needs in_scope true/false")
                elif f["in_scope"] is False and not norm(f.get("follow_up", "")):
                    errs.append(f"{tag}: out-of-scope act_on needs a follow_up bead title")
            if f.get("pre_existing") is True and not norm(f.get("follow_up", "")):
                errs.append(f"{tag}: pre_existing act_on needs a follow_up bead title (it goes to a new "
                            "bead, not this branch)")
        else:
            if len(norm(f.get("reason", ""))) < 12:
                errs.append(f"{tag}: {v} needs a written reason")
            if v == "consider" and not norm(f.get("follow_up", "")):
                errs.append(f"{tag}: consider needs a follow_up bead title")
            if protected and v in ("dismissed", "noted"):
                errs.append(f"{tag}: never-dismiss area (booking/money/sends/webhooks/tenancy/GDPR/"
                            f"prescribing/single-device data loss): must be act_on or consider, not {v}")
            if v == "noted" and dup:
                errs.append(f"{tag}: H-D0 duplicate path is graded P2 and cannot be Noted; act_on, consider "
                            "or dismissed")
            elif v == "noted" and not src.startswith("P3"):
                errs.append(f"{tag}: only a P3 may be Noted; a P1/P2 is act_on, consider or dismissed")
            if v == "dismissed" and src.startswith("P1") and not f.get("pattern"):
                errs.append(f"{tag}: dismissing a P1 needs a DISMISSAL_PATTERNS heading; a new shape stays "
                            "Consider (follow-up) until a pattern is written")
        pat = f.get("pattern")
        if pat and norm(pat).lower() not in known:
            errs.append(f"{tag}: pattern {pat!r} is not a heading in DISMISSAL_PATTERNS.md")
    want = {}
    for line in reviewer_lines:
        want[line] = want.get(line, 0) + 1
    for line, n in want.items():
        if seen.get(line, 0) != n:
            errs.append(f"reviewer finding covered {seen.get(line, 0)}x (want {n}x): {line[:100]}")
    for line in seen:
        if line not in want:
            errs.append(f"lead lists a finding no reviewer made: {line[:100]}")
    blocking = [f for f in items if f.get("verdict") == "act_on" and not f.get("_demoted")
                and not routed_out(f, scope_lock) and (not scope_lock or f.get("in_scope") is True)]
    if len(blocking) > ACT_ON_CAP and not norm(lead.get("act_on_over_cap_reason") or ""):
        errs.append(f"{len(blocking)} blocking act_on items (> {ACT_ON_CAP}): not filtering; filter harder "
                    "or give act_on_over_cap_reason")
    if round_no >= 2 and not isinstance(lead.get("premise"), dict):
        errs.append("round >= 2: premise block required (attack the premise)")
    return errs, blocking


def action_entry(f):
    if f.get("_duplicate_path"):
        scenario = norm(f.get("clinic_scenario", "")) or "n/a: structural, no clinic-scenario filter"
        grade = "P1" if clean(f["source_line"]).startswith("P1") else "P2"
        return (f"{f['source_line']}\n  graded: {grade} (H-D0 duplicate path, blocks landing)\n"
                f"  scenario: {scenario}\n  shown by: {f['demonstrated']}\n")
    return f"{f['source_line']}\n  scenario: {f['clinic_scenario']}\n  shown by: {f['demonstrated']}\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", required=True)
    ap.add_argument("--verdict", action="append", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--scope-lock", action="store_true")
    a = ap.parse_args(argv)
    try:
        lead = json.loads(Path(a.lead).read_text())
        reviewer_lines = reviewer_findings(a.verdict)
    except Exception as e:
        print(f"review-lead: REFUSED: cannot read inputs ({e})", file=sys.stderr)
        return 1
    if not isinstance(lead, dict):
        print("review-lead: REFUSED: lead JSON is not an object", file=sys.stderr)
        return 1
    if bool(lead.get("scope_lock")) != a.scope_lock:
        print(f"review-lead: REFUSED: lead scope_lock={lead.get('scope_lock')} but gate says {a.scope_lock}",
              file=sys.stderr)
        return 1
    errs, blocking = check(lead, reviewer_lines, a.round, a.scope_lock)
    if errs:
        print("review-lead: REFUSED, re-run the lead with these fixed:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    items = lead["findings"]
    out_of_scope = [f for f in items if routed_out(f, a.scope_lock)]
    demoted = [f for f in items if f.get("_demoted")]
    consider = [f for f in items if f["verdict"] == "consider" and not f.get("_demoted")]
    # Protected items not fixed on this branch go to the orchestrator, never silently into a bead.
    # So does an unresolved H-D0 the branch added: a P2 duplicate path blocks landing (LUCY-fuu2h).
    def must_escalate(f):
        return f.get("_protected") or (f.get("_duplicate_path") and f.get("pre_existing") is not True)
    escalate = [f for f in consider if must_escalate(f)] + \
               [f for f in out_of_scope if must_escalate(f)]
    follow = [f for f in consider if not must_escalate(f)] + \
             [f for f in out_of_scope if not must_escalate(f)]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = a.round
    (out / f"lead-actions-round-{n}.txt").write_text("".join(action_entry(f) for f in blocking))
    (out / f"lead-escalate-round-{n}.txt").write_text(
        "".join(f"{f['source_line']}\n  why not fixed here: {f.get('reason') or f.get('follow_up')}\n"
                for f in escalate))
    (out / f"lead-followups-round-{n}.txt").write_text(
        "".join(f"{f['follow_up']}\t{f['source_line']}\n" for f in follow))
    rows = [f"# Review lead, round {n}: {lead.get('bead', '?')}", ""]
    prem = lead.get("premise") or {}
    if prem.get("premise"):
        rows += [f"**Premise attacked:** {prem['premise']} (rounds failed on it: {prem.get('rounds_failed_on_it')})",
                 f"**Instead:** {prem.get('action')}", ""]
    for f in items:
        if f["verdict"] == "act_on" and f in blocking:
            continue
        if f.get("_demoted"):
            rows.append(f"- **p4 note** (was {f['verdict']}; no ordinary-use path, not carve-out; no fix, "
                        f"no re-review): {f['source_line']}\n  - reason: {f.get('reason') or 'ordinary-use test'}")
            continue
        if f["verdict"] == "act_on":
            label = "act_on (pre-existing)" if f.get("pre_existing") is True else "act_on (out of scope)"
        else:
            label = f["verdict"]
        why = f.get("reason") or f"{'pre-existing' if f.get('pre_existing') is True else 'scope lock'}: " \
                                 f"{f.get('follow_up')}"
        pat = f" [pattern: {f['pattern']}]" if f.get("pattern") else ""
        rows.append(f"- **{label}**{pat}: {f['source_line']}\n  - reason: {why}")
    (out / f"lead-dismissals-round-{n}.md").write_text("\n".join(rows) + "\n")
    counts = {k: sum(1 for f in items if f["verdict"] == k) for k in ("dismissed", "noted")}
    print(f"BLOCKING={len(blocking)} ESCALATE={len(escalate)} FOLLOWUPS={len(follow)} "
          f"DISMISSED={counts['dismissed']} NOTED={counts['noted']} P4={len(demoted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
