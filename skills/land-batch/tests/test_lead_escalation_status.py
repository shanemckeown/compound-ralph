"""The lead-escalation status comment is never a veto; every verdict-shaped veto still is (LUCY-b7pqo).

Case 1 is the real AestheticcNext-9kwlw comment of 2026-10-25 01:10, verbatim: the gate refused
the clean re-gate of 5f45b3c9d2 because it says "Astra FAIL" beside the tip.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))

import land_batch_safety as lbs  # noqa: E402

BEAD = "AestheticcNext-9kwlw"
TIP = "5f45b3c9d2812c0ba70556c3279fbbff8ab3f6db"
EVIDENCE = str(lbs.EVIDENCE_ROOT / "review/9kwlw-independent-r2/lead-escalate-round-2.txt")
HEAD = f"needs input: {BEAD} escalated 2 protected item(s): {EVIDENCE}"
REAL_0110 = (f"{HEAD} — independent r2 @5f45b3c9d2: Opus PASS, Astra FAIL (1 P2, pre-existing → follow-up "
             "AestheticcNext-4rr06). Lead BLOCKING=0 ESCALATE=2 FIXED=2. Both escalations are the r1 SKIP "
             "LOCKED P3 (raised by both r1 seats at one sev/file:line), closed by both r2 seats with real-pg "
             "proof. The checker's G3/A5 ref-ambiguity rule escalates them, and they are branch-owned so not "
             "clearable. With Orchestrator23 for a ruling. HOLD at 5f45b3c9d2.")


def _comment(ts, body):
    return f"[Encrypted-S] at {ts}\n" + "".join(f"  {line}\n" for line in body.splitlines()) + "\n"


def _vetoes(body, bead=BEAD):
    return lbs.comment_vetoes(_comment("2026-10-25 01:10", body), TIP, bead)


def test_case1_real_9kwlw_status_comment_is_not_a_veto():
    assert lbs.is_lead_escalation_status(REAL_0110, BEAD)
    assert _vetoes(REAL_0110) == []


@pytest.mark.parametrize("body", [
    # (2) free text, not the exact prefix
    f"needs input: FAIL @{TIP[:10]}",
    f"needs input: {BEAD} escalated: Astra FAIL at 5f45b3c9d2",
    # (4) exact first line + a later review-gate mirror line
    f"{HEAD}\nFAIL @{TIP}",
    # (5) the verdict on the rest of line 1
    f"{HEAD} — FAIL @{TIP[:10]}",
    f"{HEAD} — Astra FAILED @ {TIP[:7]}",
    f"{HEAD} — Verdict: **FAIL** @{TIP[:10]}",
    f"{HEAD} — Astra FAIL: {TIP[:10]}",
    # (6) another bead (no prefix match), or a path outside the evidence root
    REAL_0110.replace(BEAD, "AestheticcNext-9kwlw2", 1),
    REAL_0110.replace(BEAD, "AestheticcNext-9kwl", 1),
    REAL_0110.replace(EVIDENCE, "/tmp/lead-escalate-round-2.txt", 1),
    REAL_0110.replace(EVIDENCE, str(lbs.EVIDENCE_ROOT / "../reviews/x.txt"), 1),
    REAL_0110.replace(EVIDENCE, str(lbs.EVIDENCE_ROOT) + "-other/x.txt", 1),
    # (7) BLOCKING>=1 anywhere
    REAL_0110.replace("BLOCKING=0", "BLOCKING=1"),
    f"{REAL_0110}\nlead re-run: BLOCKING=2 ESCALATE=0",
    # a withdrawal line
    f"{REAL_0110}\nVOID: superseded by r3",
    f"{REAL_0110}\nWithdrawn — superseded by r3",
    # only a bare prose FAIL is exempt: any other veto token still vetoes
    REAL_0110.replace("Astra FAIL", "Astra FAILED"),
    REAL_0110.replace("Astra FAIL", "Astra review VOIDED"),
])
def test_cases_2_4_5_6_7_still_veto(body):
    assert not lbs.is_lead_escalation_status(body, BEAD)
    assert len(_vetoes(body)) == 1, body


def test_case3_real_fail_mirror_still_vetoes_beside_the_status_line():
    text = (_comment("2026-10-25 01:10", REAL_0110)
            + _comment("2026-10-25 01:20", f"FAIL @{TIP}\n/some/REVIEW_9kwlw_astra_r3.md\nP2 lib/x.ts:9 — y — z"))
    found = lbs.comment_vetoes(text, TIP, BEAD)
    assert [f for _, f, _ in found] == [f"FAIL @{TIP}"]


def _gate_record(tmp_path, mtime):
    root = tmp_path / "reviews"
    root.mkdir()
    rec = root / "REVIEW_9kwlw_gate_5f45b3c9d2_20261025T030000Z.md"
    rec.write_text(f"REVIEWING: {BEAD} @ {TIP}\n\n{lbs.GATE_SIGNATURE} BLOCKING=0 ESCALATE=0\n\nVerdict: PASS\n")
    os.utime(rec, (mtime, mtime))
    return root


def test_lander_lands_past_a_later_status_comment_but_not_a_later_mirror(tmp_path):
    root = _gate_record(tmp_path, 1792897200)  # 2026-10-25 03:00 UTC
    passed = _comment("2026-10-25 03:01", f"PASS @{TIP}")
    status = _comment("2026-10-25 03:10", REAL_0110)  # after the approval: only the exemption lets it land
    ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=root, comments_text=passed + status)
    assert ok is True, ev
    mirror = _comment("2026-10-25 03:10", f"{HEAD}\nFAIL @{TIP}")
    ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=root, comments_text=passed + mirror)
    assert ok is False and "FAIL" in ev, ev


def test_withdrawal_is_one_definition_shared_with_review_gate():
    sys.path.insert(0, str(ROOT.parent.parent / "references" / "review-lead"))
    import review_gate
    assert review_gate.WITHDRAWAL is lbs.WITHDRAWAL_RE
