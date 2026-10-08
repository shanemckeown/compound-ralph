"""review_pass_for: the lead's verdict decides, not a raw reviewer FAIL (8 Oct).

Fixtures are real ``bd comments`` output captured 2026-10-08:
  fixtures/bd_comments_q2lod12.txt  AestheticcNext-q2lod.12 (Astra FAIL -> lead BLOCKING=0
                                    -> escalations accepted -> PASS @tip -> PASS VOID)
  fixtures/bd_comments_uynmv.txt    AestheticcNext-uynmv (PASS line quoting "raw FAIL 4 P2";
                                    false-held 7 Oct)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))

import land_batch_safety as lbs  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
Q2_TIP = "089d0d6b6a5ea492d3af648d7d53248f68447c81"
UY_TIP = "a920dd5701e1243dafb5ca428f73d05aac3b65f6"
VOID_HEADER = "[Encrypted-S] at 2026-10-08 11:12"


def _q2(include_void=True):
    text = (FIXTURES / "bd_comments_q2lod12.txt").read_text(encoding="utf-8")
    return text if include_void else text[: text.index(VOID_HEADER)]


def _check(bead, tip, comments, tmp_path):
    return lbs.review_pass_for(bead, tip, reviews_dir=tmp_path / "no-reviews", comments_text=comments)


def _comment(ts, body):
    return f"[Encrypted-S] at {ts}\n" + "".join(f"  {line}\n" for line in body.splitlines()) + "\n"


ASTRA_FAIL = (f"ASTRA XHIGH REVIEW 2026-10-08 (tip {Q2_TIP}, round-5 delta): FAIL — 0 P1, 1 P2, 1 P3. "
              "P2 rebuild-booking-confirmation.ts:85 ...")
LEAD_0_0 = (f"REVIEW LEAD 2026-10-08 (tip {Q2_TIP}, review_lead_check.py passed): "
            "BLOCKING=0, ESCALATE=0, NOTED=1. F1 -> consider.")
LEAD_0_3 = (f"REVIEW LEAD 2026-10-08 (tip {Q2_TIP}, review_lead_check.py passed): "
            "BLOCKING=0, ESCALATE=3, NOTED=1. Escalations are keyword-routed.")
LEAD_1_0 = (f"REVIEW LEAD 2026-10-08 (tip {Q2_TIP}, review_lead_check.py passed): "
            "BLOCKING=1 ESCALATE=0 FOLLOWUPS=0 DISMISSED=2 NOTED=0 P4=0")


# --- real fixtures ---------------------------------------------------------

def test_q2lod12_lead_graded_astra_fail_down_then_pass_at_tip_lands(tmp_path):
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, _q2(include_void=False), tmp_path)
    assert passed is True
    assert evidence == f"bead comment: PASS @{Q2_TIP}"


def test_q2lod12_later_pass_void_line_holds_again(tmp_path):
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, _q2(), tmp_path)
    assert passed is False
    assert "VOID" in evidence


def test_q2lod12_older_tip_with_raw_fail_and_no_lead_still_holds(tmp_path):
    passed, evidence = _check("AestheticcNext-q2lod.12", "8bae8408f", _q2(), tmp_path)
    assert passed is False
    assert evidence.startswith("FAIL recorded for this tip")


def test_uynmv_pass_line_quoting_raw_fail_is_a_pass(tmp_path):
    comments = (FIXTURES / "bd_comments_uynmv.txt").read_text(encoding="utf-8")
    passed, evidence = _check("AestheticcNext-uynmv", UY_TIP, comments, tmp_path)
    assert passed is True
    assert evidence == f"bead comment: PASS @{UY_TIP}"


# --- override rules on real comment shapes ---------------------------------

def test_lead_blocking0_escalate0_after_raw_fail_is_a_pass(tmp_path):
    comments = _comment("2026-10-08 10:32", ASTRA_FAIL) + _comment("2026-10-08 10:36", LEAD_0_0)
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)
    assert passed is True
    assert "REVIEW LEAD" in evidence


def test_lead_with_open_escalations_needs_a_pass_line(tmp_path):
    comments = _comment("2026-10-08 10:32", ASTRA_FAIL) + _comment("2026-10-08 10:36", LEAD_0_3)
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)
    assert passed is False
    assert "escalations not cleared" in evidence
    comments += _comment("2026-10-08 10:37", f"PASS @{Q2_TIP}")
    assert _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)[0] is True


def test_lead_with_blocking_findings_holds(tmp_path):
    comments = _comment("2026-10-08 10:32", ASTRA_FAIL) + _comment("2026-10-08 10:36", LEAD_1_0)
    assert _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)[0] is False


def test_bare_pass_line_does_not_override_uncleared_raw_fail(tmp_path):
    comments = _comment("2026-10-08 10:32", ASTRA_FAIL) + _comment("2026-10-08 10:37", f"PASS @{Q2_TIP}")
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)
    assert passed is False
    assert "ASTRA XHIGH REVIEW" in evidence


def test_pass_line_citing_lead_blocking0_overrides_raw_fail(tmp_path):
    pass_body = (f"PASS @{Q2_TIP}\nINDEPENDENT REVIEW: Astra xhigh (raw FAIL 4 P2) + fresh Sonnet (PASS); "
                 "review lead BLOCKING=0 ESCALATE=0 FOLLOWUPS=1 DISMISSED=3 NOTED=0 P4=0.")
    comments = _comment("2026-10-08 10:32", ASTRA_FAIL) + _comment("2026-10-08 10:40", pass_body)
    assert _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)[0] is True


def test_raw_fail_after_last_pass_still_vetoes(tmp_path):
    comments = (_comment("2026-10-08 10:32", ASTRA_FAIL)
                + _comment("2026-10-08 10:36", LEAD_0_0)
                + _comment("2026-10-08 10:37", f"PASS @{Q2_TIP}")
                + _comment("2026-10-08 11:00", f"FRESH SONNET REVIEW (tip {Q2_TIP}): FAIL — 1 P1"))
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)
    assert passed is False
    assert "FRESH SONNET REVIEW" in evidence


def test_raw_reviewer_pass_does_not_override_raw_fail(tmp_path):
    comments = (_comment("2026-10-08 10:32", ASTRA_FAIL)
                + _comment("2026-10-08 10:34", f"FRESH SONNET REVIEW 2026-10-08 (tip {Q2_TIP}): PASS — 0 P1, 0 P2"))
    assert _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)[0] is False


def test_pass_on_an_older_tip_does_not_count(tmp_path):
    comments = _comment("2026-10-08 10:37", "PASS @71fd16214")
    passed, evidence = _check("AestheticcNext-q2lod.12", Q2_TIP, comments, tmp_path)
    assert passed is False
    assert evidence == "no recorded review PASS for this tip"


def test_review_file_pass_still_counts(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "REVIEW_q2lod12.md").write_text(
        f"# Review: AestheticcNext-q2lod.12\n\ntip {Q2_TIP[:9]}\n\n## Verdict: PASS\n", encoding="utf-8")
    passed, evidence = lbs.review_pass_for("AestheticcNext-q2lod.12", Q2_TIP, reviews_dir=reviews,
                                           comments_text="")
    assert passed is True
    assert evidence.endswith("REVIEW_q2lod12.md")


# --- beads dir derivation ----------------------------------------------------

def test_beads_dir_from_prefix_and_explicit_override():
    assert lbs.beads_dir_for("AestheticcNext-q2lod.12", {}) == str(
        Path("~/Documents/GitReBase/AestheticcNext/.beads").expanduser())
    assert lbs.beads_dir_for("LUCY-58ujm", {}) == str(Path("~/Documents/Obsidian/.beads").expanduser())
    assert lbs.beads_dir_for("AestheticcNext-x", {"BEADS_DIR": "/explicit"}) == "/explicit"


def test_bead_comments_queries_the_repo_beads_dir_when_beads_dir_unset(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["beads_dir"] = cmd, kwargs["env"].get("BEADS_DIR")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.delenv("BEADS_DIR", raising=False)
    monkeypatch.delenv("LAND_SKIP_BEAD_COMMENTS", raising=False)
    monkeypatch.setattr(lbs.subprocess, "run", fake_run)
    lbs._bead_comments("AestheticcNext-uynmv")
    assert seen["cmd"] == ["bd", "comments", "AestheticcNext-uynmv"]
    assert seen["beads_dir"] == str(Path("~/Documents/GitReBase/AestheticcNext/.beads").expanduser())

    monkeypatch.setenv("BEADS_DIR", "/explicit/.beads")
    lbs._bead_comments("AestheticcNext-uynmv")
    assert seen["beads_dir"] == "/explicit/.beads"
