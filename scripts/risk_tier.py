#!/usr/bin/env python3
"""risk_tier.py: which landing gate set a diff needs, decided from its paths (LUCY-z3ykz.1).

    risk_tier.py --repo PATH [--base origin/main] [--json]   # paths = git diff base...HEAD
    git diff --name-only origin/main...HEAD | risk_tier.py [--json]   # paths on stdin

Prints `tier=light` or `tier=full` plus one reason line per path that forced full.
classify(paths) -> (tier, reasons) is the importable form; /land-batch calls it on the
combined batch diff, so the agent never picks the tier itself.

light: targeted jest + tsc + lint. full: that plus the full pg lane + test:cloudbuild.
The default is full (fail closed). A diff is light ONLY when every path is UI, styling,
copy or a test of those, and no path names a sensitive surface (money, patient data,
consent, tenancy, comms/sending, auth). An empty diff is full: nothing to classify means
something went wrong upstream, not that nothing can break.

This is deliberately not gate-tier.py: that one classifies an ACTION's consequence
(T0/T1/T2) for dispatch and deploy decisions. This one only picks a test set.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# UI / styling / copy, plus the test trees that cover them. Under components/ only
# presentation files count: a components/**/*.ts helper is logic (upload-helper.ts uploads
# patient photos), so it is full.
COMPONENT_PREFIX = "components/"
COMPONENT_LIGHT_SUFFIXES = (".tsx", ".jsx", ".css", ".scss", ".mdx", ".md", ".svg")
LIGHT_PREFIXES = (
    "styles/",
    "content/",
    "copy/",
    "tests/components/",
    "__tests__/components/",
)
LIGHT_SUFFIXES = (".css", ".scss")
# public/ static text and images only: public/*.js (a service worker, embed.js) is code.
PUBLIC_TEXT_SUFFIXES = (".txt", ".md", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico")
# Bookkeeping every /ship, bd or ratchet commit carries. Named files only, never a whole
# directory. The beads exports ARE a test input (tests/config-effect reads KNOWN_GAPS beads),
# so tier_gates.py adds that suite to the gates whenever one of them changes.
BEADS_EXPORTS = {".beads/issues.jsonl", ".beads/export-state.json", "issues.jsonl"}
# Regenerated and verified by /land-batch's ratchet helper on every land that moves a line.
NEUTRAL_EXACT = {"CHANGELOG.md", "VERSION", "tests/meta/file-length-baseline.json"} | BEADS_EXPORTS

# A component whose source writes data or reaches money/auth code is full whatever its name
# (Astra round 2: components/terminal/mobile-terminal-bridge.tsx creates payment intents,
# components/clients/CommunicationPreferences.tsx writes marketing consent). Checked on the
# file at HEAD when classify() is given the repo; a file it cannot read there is full.
WRITES_DATA = re.compile(
    r"fetch\(|useMutation|axios|mutate\(|apiClient|['\"`]/api/"
    r"|@/lib/(?:payments|stripe|services|auth|db)\b"
)

# Any path naming one of these is full, even under components/ (components/payments/X.tsx).
SENSITIVE = re.compile(
    r"stripe|payment|billing|consent|patient|clinical|tenant|business_id|sms|email|send|comms|auth"
    r"|price|pricing|membership|voucher|deposit|refund|invoice|checkout|photo|upload|gdpr",
    re.IGNORECASE,
)

# Patient-data domains (LUCY-z3ykz.6): any file under these is full whatever its name or
# source, because writes reach patient records indirectly (Codex round 3:
# FaceMappingViewer.tsx saves through a queue helper that neither rule above sees).
# Picked by listing AestheticcNext/components/ on 13 Oct 2026.
PATIENT_DOMAINS = (
    "components/face-mapping/",  # face maps, injection points and patient photos
    "components/photos/",  # before/after patient photos
    "components/photo-import/",  # bulk import of patient photos
    "components/dialogs/",  # only EditPhotoDialog.tsx, which edits a patient photo
    "components/prescriptions/",  # prescribing pathway
    "components/glp1/",  # GLP-1 programmes: BMI, dosing, check-ins
    "components/forms/",  # consent and medical-history forms
    "components/kiosk/",  # in-clinic patient form completion
    "components/portal/",  # client portal: the patient's own records
    "components/clients/",  # client record, medical history, notes
    "components/appointments/",  # treatment notes, contraindication checker
    "components/calendar/",  # creates and edits appointments for clients
    "components/batch-tracking/",  # which product batch went into which patient
    "components/csv-import/",  # bulk import of client records
    # Astra own-gate round 1: these also carry patient data under innocent file names.
    "components/booking/",  # customer-form.tsx takes patient details and booking notes
    "components/plan-builder/",  # a client's care-plan timing
    "components/voice/",  # VoiceInput.tsx records and transcribes dictation
    "components/command-center/",  # UnifiedTimeline.tsx shows client conversation transcripts
    "components/rebooking/",  # lists named patients and their treatments
    "components/marketing/",  # UnifiedLibrary.tsx composes posts from clinical photos
    # Round 2: render-only, but they show patient records.
    "components/advisory/",  # morning briefing names overdue patients
    "components/clinic-iq/",  # suggestion cards quote clinical notes
    "components/reports/",  # report tables list patient names and visits
    "components/content/",  # BALibrary.tsx groups patient before/after photos by client
    "components/reviews/",  # ClientFeedbackList.tsx lists each client's feedback
)
# Not listed: leads/ (prospects, not patients), audio/ (no consumers). Landing, blog, ui
# and help stay light.


def component_source_reason(path, repo):
    try:
        text = (Path(repo) / path).read_text(errors="replace")
    except OSError:
        return f"{path}: component not readable at HEAD (deleted?) (default full)"
    hit = WRITES_DATA.search(text)
    return f"{path}: component writes data or reaches money/auth code ({hit.group(0)})" if hit else None


def path_reason(path, repo=None):
    """None if this path is light, else why it forces full."""
    if path in NEUTRAL_EXACT:
        return None
    hit = SENSITIVE.search(path)
    if hit:
        return f"{path}: names a sensitive surface ({hit.group(0)})"
    if path.startswith(PATIENT_DOMAINS):
        return f"{path}: patient-data domain ({path.split('/')[1]})"
    if path.startswith(COMPONENT_PREFIX):
        if path.endswith(COMPONENT_LIGHT_SUFFIXES):
            return component_source_reason(path, repo) if repo and path.endswith((".tsx", ".jsx")) else None
        return f"{path}: component logic, not presentation (default full)"
    if path.startswith(LIGHT_PREFIXES) or path.endswith(LIGHT_SUFFIXES):
        return None
    if path.startswith("public/") and path.lower().endswith(PUBLIC_TEXT_SUFFIXES):
        return None
    return f"{path}: not UI/style/copy (default full)"


def classify(paths, repo=None):
    """Return (tier, reasons). tier is 'light' or 'full'; reasons lists what forced full.
    With repo (a checkout at the tree being landed), component sources are read too;
    without it (paths on stdin) only path rules apply."""
    paths = [p.strip() for p in paths if p and p.strip()]
    if not paths:
        return "full", ["empty diff: nothing to classify (fail closed)"]
    reasons = [r for r in (path_reason(p, repo) for p in paths) if r]
    return ("full", reasons) if reasons else ("light", [])


def diff_paths(repo, base):
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--no-renames", "--name-only", f"{base}...HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.split("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="git checkout to diff; without it, paths are read from stdin")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    paths = diff_paths(args.repo, args.base) if args.repo else sys.stdin.read().split("\n")
    tier, reasons = classify(paths, args.repo)
    if args.json:
        print(json.dumps({"tier": tier, "reasons": reasons,
                          "path_count": len([p for p in paths if p.strip()])}))
    else:
        print(f"tier={tier}")
        for r in reasons:
            print(f"  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
