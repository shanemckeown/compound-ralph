"""review_pass_for: the lead's verdict decides, not a raw reviewer FAIL (8 Oct).

Fixtures are real ``bd comments`` output captured 2026-10-08:
  fixtures/bd_comments_q2lod12.txt  AestheticcNext-q2lod.12 (Astra FAIL -> lead BLOCKING=0
                                    -> escalations accepted -> PASS @tip -> PASS VOID)
  fixtures/bd_comments_uynmv.txt    AestheticcNext-uynmv (PASS line quoting "raw FAIL 4 P2";
                                    false-held 7 Oct)
"""

import os
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


# --- independent_pass_for: what discovery accepts for auto_land (LUCY-qymwm) ---------

IND_TIP = "7649f85281ef8012cae27cd56d1a3f6cea301c2c"


def _reviews(tmp_path, verdict="PASS", bead="AestheticcNext-q41ar", tip=IND_TIP):
    """A review-gate record (what review-gate.sh writes)."""
    root = tmp_path / "reviews"
    root.mkdir(parents=True, exist_ok=True)
    (root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md").write_text(
        f"REVIEWING: {bead} @ {tip}\n\n{lbs.GATE_SIGNATURE} BLOCKING=0 ESCALATE=0\n\nVerdict: {verdict}\n")
    return root


def test_independent_pass_needs_exact_first_line_and_review_file(tmp_path):
    root = _reviews(tmp_path)
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                      comments_text=_comment("2026-10-23 09:00", f"PASS @{IND_TIP}\nreview-gate: BLOCKING=0 ESCALATE=0"))
    assert ok is True, ev
    assert ev.startswith(f"PASS @{IND_TIP} + ")


def test_independent_pass_accepts_a_tip_prefix_but_nothing_after_it_on_the_line(tmp_path):
    root = _reviews(tmp_path)
    ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                     comments_text=_comment("2026-10-23 09:00", "PASS @7649f85281"))
    assert ok is True
    ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                     comments_text=_comment("2026-10-23 09:00", "PASS @7649f85281 (own gate)"))
    assert ok is False


def test_independent_pass_own_gate_prose_is_not_a_pass(tmp_path):
    root = _reviews(tmp_path)
    own = ("r10 PUSHED: goal/aestheticcnext-q41ar @7649f85281. Own-gate review (NOT independent): "
           "Astra xhigh PASS 0 findings, Opus h2h PASS 0, review lead BLOCKING=0 ESCALATE=0.")
    assert lbs.review_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=tmp_path / "none",
                               comments_text=_comment("2026-10-22 20:48", own))[0] is True  # the old hole
    ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                     comments_text=_comment("2026-10-22 20:48", own))
    assert ok is False


def test_independent_pass_held_by_later_void_or_fail_review_file_only(tmp_path):
    root = _reviews(tmp_path)
    comments = (_comment("2026-10-23 09:00", f"PASS @{IND_TIP}")
                + _comment("2026-10-23 09:30", f"PASS @{IND_TIP} VOID: wrong brief"))
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root, comments_text=comments)
    assert ok is False and "VOID" in ev
    fail_root = _reviews(tmp_path / "f", verdict="FAIL")
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=fail_root,
                                      comments_text=_comment("2026-10-23 09:00", f"PASS @{IND_TIP}"))
    assert ok is False and "Verdict: PASS" in ev


def test_independent_pass_not_reset_by_own_gate_prose_after_a_fail(tmp_path):
    """Astra r2: old PASS pair, then FAIL, then builder prose citing review_lead_check.py passed."""
    root = _reviews(tmp_path)
    old = (tmp_path / "reviews" / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md")
    os.utime(old, (1761123600, 1761123600))  # 2025-10-22 09:00 UTC, well before the comments
    comments = (_comment("2026-10-23 09:00", f"PASS @{IND_TIP}")
                + _comment("2026-10-23 09:10", f"ASTRA REVIEW (tip {IND_TIP}): FAIL — 1 P1")
                + _comment("2026-10-23 09:20", f"Own-gate (NOT independent) tip {IND_TIP}: "
                                               "review_lead_check.py passed: BLOCKING=0 ESCALATE=0"))
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root, comments_text=comments)
    assert ok is False and "after the last review-gate PASS" in ev


def test_independent_pass_vetoed_by_newer_fail_file_in_any_format(tmp_path):
    for fail_body in ("FAIL\nP1 lib/x.ts:1 — bad — fix\n",
                      '```review-verdict\n{"verdict": "FAIL", "findings": [{"severity": "P1", "text": "x"}]}\n```\n',
                      "**Verdict: FAIL**\n"):
        root = _reviews(tmp_path / str(abs(hash(fail_body))))
        os.utime(root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md", (1761123600, 1761123600))
        newer = root / "REVIEW_q41ar_astra_r11.md"
        newer.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {IND_TIP}\nAestheticcNext-q41ar\n" + fail_body)
        ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                          comments_text=_comment("2025-10-22 09:30", f"PASS @{IND_TIP}"))
        assert ok is False, fail_body


def test_independent_pass_after_fail_with_fresh_pass_pair_lands(tmp_path):
    root = _reviews(tmp_path)  # gate file written now
    comments = (_comment("2026-10-22 21:14", f"FAIL @{IND_TIP}\nASTRA REVIEW (tip {IND_TIP}): FAIL — 0 P1, 1 P2")
                + _comment("2099-01-01 00:00", f"PASS @{IND_TIP}\nreview-gate round 10: BLOCKING=0 ESCALATE=0"))
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root, comments_text=comments)
    assert ok is True, ev


def test_no_env_can_stand_in_for_live_comments_or_reviews(monkeypatch):
    """Astra r2/r3: a fixture env replaced live comments in production discovery. It is gone."""
    monkeypatch.setenv("LAND_BEAD_COMMENTS_DIR", "/tmp/fake")
    monkeypatch.setattr(lbs.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "LIVE", ""))
    assert lbs._bead_comments("AestheticcNext-q41ar") == "LIVE"


def test_reviewer_pass_file_without_gate_signature_is_not_approval(tmp_path):
    root = tmp_path / "reviews"
    root.mkdir()
    (root / "REVIEW_q41ar_sonnet.md").write_text(f"REVIEWING: goal/x @ {IND_TIP}\nAestheticcNext-q41ar\nVerdict: PASS\n")
    ok, ev = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                      comments_text=_comment("2099-01-01 00:00", f"PASS @{IND_TIP}"))
    assert ok is False and "no review-gate record" in ev


def test_gate_record_for_another_tip_with_this_tip_as_base_is_not_approval(tmp_path):
    other = "d60331cb15581e5dc1ff7f6551f050e10e6c5550"
    root = _reviews(tmp_path, tip=other)
    (root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md").write_text((root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md").read_text()
                                               + f"Delta {IND_TIP}..{other}\n")
    ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                     comments_text=_comment("2099-01-01 00:00", f"PASS @{IND_TIP}"))
    assert ok is False


def test_any_later_comment_naming_the_tip_with_a_veto_token_holds(tmp_path):
    root = _reviews(tmp_path)
    os.utime(root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md", (1761123600, 1761123600))
    for veto in (f"FAIL @{IND_TIP}", f"**Verdict: FAIL** (tip {IND_TIP})", f"PASS @{IND_TIP}\nPASS @{IND_TIP} VOID: wrong brief",
                 f"lead tip {IND_TIP}: BLOCKING=1 ESCALATE=0"):
        comments = _comment("2025-10-22 09:30", f"PASS @{IND_TIP}") + _comment("2025-10-22 09:40", veto)
        ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root, comments_text=comments)
        assert ok is False, veto


def test_fail_review_quoting_the_gate_signature_still_vetoes(tmp_path):
    """Astra r4: a FAIL review that quotes the gate signature was skipped as a 'gate record'."""
    root = _reviews(tmp_path)
    os.utime(root / "REVIEW_q41ar_gate_7649f85281_20261023T000000Z.md", (1761123600, 1761123600))
    (root / "REVIEW_q41ar_astra_r11.md").write_text(
        f"REVIEWING: goal/aestheticcnext-q41ar @ {IND_TIP}\nAestheticcNext-q41ar\n"
        f"The gate wrote '{lbs.GATE_SIGNATURE} BLOCKING=0' but:\nFAIL\nP1 lib/pay.ts:9 — bad — fix\n")
    ok, _ = lbs.independent_pass_for("AestheticcNext-q41ar", IND_TIP, reviews_dir=root,
                                     comments_text=_comment("2025-10-22 09:30", f"PASS @{IND_TIP}"))
    assert ok is False


# --- LUCY-txfbf: only a review OF bead@tip vetoes it, never a harness review quoting it ------

TX_TIP = "4f1c34f25e75593f87ca09fc2b4639c63b174e23"


def _tx_root(tmp_path, name, body):
    root = tmp_path / "reviews"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body)
    return root


def test_txfbf_harness_review_quoting_the_bead_as_fixture_does_not_veto(tmp_path):
    root = _tx_root(tmp_path, "REVIEW_e3opf_structured_astra-high_r3_2026-10-24.md",
                    "Verdict: FAIL\n\nREVIEWING: e3opf-structured @ 5acadb10c839b61435d08494cf085e1a2222edfe\n\nFAIL\n"
                    "- **3bm73 r1–r6:** all ten files parse at tip `4f1c34f2`; AestheticcNext-3bm73 replayed.\n")
    assert lbs.review_file_vetoes(root, "AestheticcNext-3bm73", TX_TIP) == []
    path = root / "REVIEW_e3opf_structured_astra-high_r3_2026-10-24.md"
    assert lbs.is_other_branch_review(path, path.read_text(), "AestheticcNext-3bm73", TX_TIP) is True


def test_txfbf_real_fail_at_the_tip_still_vetoes_in_every_echo_form(tmp_path):
    for i, echo in enumerate((f"REVIEWING: goal/aestheticcnext-3bm73 @ {TX_TIP}",
                              "REVIEWING: goal/aestheticcnext-3bm73@4f1c34f",
                              "  REVIEWING:goal/aestheticcnext-3bm73-r2 @ 4f1c34f25e",
                              f"REVIEWING: AestheticcNext-3bm73 @ {TX_TIP}",
                              f"REVIEWING: goal/aestheticcnext-3bm73 @ 1111111\nREVIEWING: goal/aestheticcnext-3bm73 @ {TX_TIP}")):
        root = _tx_root(tmp_path / str(i), "REVIEW_3bm73_astra-xhigh_r8.md", f"{echo}\nFAIL\nP1 lib/x.ts:1 — bad — fix\n")
        assert len(lbs.review_file_vetoes(root, "AestheticcNext-3bm73", TX_TIP)) == 1, echo
    # The file name carries the bead when the echo's branch is a bare worktree name.
    root = _tx_root(tmp_path / "wt", "REVIEW_3bm73_opus_r8.md", f"REVIEWING: wt-3 @ {TX_TIP[:9]}\n3bm73 FAIL\n")
    assert len(lbs.review_file_vetoes(root, "AestheticcNext-3bm73", TX_TIP)) == 1
    # And the lander's own scan holds the tip on it.
    root = _tx_root(tmp_path / "ind", "REVIEW_3bm73_astra-xhigh_r8.md",
                    f"REVIEWING: goal/aestheticcnext-3bm73 @ {TX_TIP}\nFAIL\n")
    ok, _ = lbs.independent_pass_for("AestheticcNext-3bm73", TX_TIP, reviews_dir=root,
                                     comments_text=_comment("2025-10-22 09:30", f"PASS @{TX_TIP}"))
    assert ok is False


def test_txfbf_legacy_review_with_no_echo_still_vetoes_by_its_name(tmp_path):
    root = _tx_root(tmp_path, "REVIEW_3bm73_legacy.md", f"AestheticcNext-3bm73 at {TX_TIP[:10]}\n**Verdict: FAIL**\n")
    assert len(lbs.review_file_vetoes(root, "AestheticcNext-3bm73", TX_TIP)) == 1
    # A bead-named file with an older echo and a FAIL naming the current tip still vetoes.
    other = _tx_root(tmp_path / "o", "REVIEW_3bm73_r2.md",
                     f"REVIEWING: goal/aestheticcnext-3bm73 @ 1111111\nnow at {TX_TIP}\nFAIL\n")
    assert len(lbs.review_file_vetoes(other, "AestheticcNext-3bm73", TX_TIP)) == 1


def test_txfbf_dashed_legacy_name_and_bare_echo_veto_in_lander_and_scan(tmp_path):
    root = _tx_root(tmp_path, "REVIEW_1b6a3-32_astra.md",
                    f"REVIEWING: wt @ 2222222\nAestheticcNext-1b6a3.32 tip {TX_TIP[:9]}\nFAIL\n")
    assert len(lbs.review_file_vetoes(root, "AestheticcNext-1b6a3.32", TX_TIP)) == 1
    root = _tx_root(tmp_path / "h", "REVIEW_3bm73_opus_r8.md", f"REVIEWING: HEAD @ {TX_TIP}\n3bm73 FAIL\n")
    ok, _ = lbs.independent_pass_for("AestheticcNext-3bm73", TX_TIP, reviews_dir=root,
                                     comments_text=_comment("2025-10-22 09:30", f"PASS @{TX_TIP}"))
    assert ok is False


# Attempt 2: the old veto rule stands; only a review that echoes ANOTHER branch is exempt.

def _vetoes(tmp_path, name, body, bead="AestheticcNext-3bm73", tip=TX_TIP):
    return lbs.review_file_vetoes(_tx_root(tmp_path, name, body), bead, tip)


def test_txfbf_noyau_corpus_shapes_with_no_echo_still_veto(tmp_path):
    tip = "20696a4f5968424a8a092da94086bf6a54d48930"
    body = ("# Review — AestheticcNext-noyau.2 @ 20696a4f5\n\nVerdict: FAIL\n\n"
            "- Bead: AestheticcNext-noyau.2 (help cards)\n"
            "- Branch: goal/aestheticcnext-noyau.2, tip 20696a4f5 (20696a4f5968424a8a092da94086bf6a54d48930)\n")
    for i, name in enumerate(("REVIEW_noyau2_20261006.md", "REVIEW_noyau2_r2_20261006.md", "REVIEW_x_20261006.md")):
        assert len(_vetoes(tmp_path / str(i), name, body, "AestheticcNext-noyau.2", tip)) == 1, name
    # Even with an echo of another branch, a Bead:/Branch: field naming the bead keeps the veto.
    echoed = "REVIEWING: other-branch @ 1234567\n" + body
    assert len(_vetoes(tmp_path / "e", "REVIEW_x_20261006.md", echoed, "AestheticcNext-noyau.2", tip)) == 1


def test_txfbf_annotated_bold_and_backticked_echoes_of_the_bead_still_veto(tmp_path):
    for i, echo in enumerate((f"REVIEWING: goal/aestheticcnext-3bm73 @ {TX_TIP} (round 9)",
                              f"**REVIEWING:** goal/aestheticcnext-3bm73 @ {TX_TIP}",
                              f"REVIEWING: `goal/aestheticcnext-3bm73` @ `{TX_TIP[:10]}`",
                              f"> - REVIEWING: goal/aestheticcnext-3bm73@{TX_TIP} round 9",
                              f"Reviewing: goal/aestheticcnext-3bm73 @ {TX_TIP}")):
        body = f"{echo}\nFAIL\nP2 lib/x.ts:1 — bad — fix\n"
        assert len(_vetoes(tmp_path / str(i), "REVIEW_allure_followup_astra_r9.md", body)) == 1, echo
        lbs_root = _tx_root(tmp_path / f"l{i}", "REVIEW_allure_followup_astra_r9.md", body)
        ok, _ = lbs.independent_pass_for("AestheticcNext-3bm73", TX_TIP, reviews_dir=lbs_root,
                                         comments_text=_comment("2025-10-22 09:30", f"PASS @{TX_TIP}"))
        assert ok is False, echo


def test_txfbf_doubtful_echoes_keep_the_veto(tmp_path):
    prose = f"3bm73 replay at {TX_TIP[:8]}: FAIL\n"
    for i, echo in enumerate(("REVIEWING: e3opf-structured (no sha)",           # unparseable echo
                              "REVIEWING: HEAD @ 3333333",                      # bare HEAD, another sha
                              f"REVIEWING: some-worktree @ {TX_TIP}",           # at this very tip
                              f"REVIEWING: e3opf @ 5acadb10c8\nREVIEWING: goal/aestheticcnext-3bm73 @ 1111111")):
        assert len(_vetoes(tmp_path / str(i), "REVIEW_e3opf_r9.md", f"{echo}\n{prose}")) == 1, echo
    # A dotted child's parent epic branch carries the child.
    body = "REVIEWING: goal/aestheticcnext-noyau @ 5acadb10c8\nnoyau.2 at 20696a4f5: FAIL\n"
    assert len(_vetoes(tmp_path / "p", "REVIEW_epic_r1.md", body, "AestheticcNext-noyau.2",
                       "20696a4f5968424a8a092da94086bf6a54d48930")) == 1


def test_txfbf_real_harness_review_files_do_not_veto_3bm73(tmp_path):
    reviews = Path("/Users/shane/Documents/Obsidian/Aestheticc/Ops/reviews")
    names = [f"REVIEW_e3opf_structured_astra-high_r{n}_2026-10-24.md" for n in (3, 4, 5, 6)]
    names.append("REVIEW_txfbf_astra-high_independent_r1_2026-10-25.md")
    present = [n for n in names if (reviews / n).is_file()]
    if not present:
        import pytest
        pytest.skip("Ops/reviews corpus not on this machine")
    root = tmp_path / "reviews"
    root.mkdir()
    for n in present:
        (root / n).write_text((reviews / n).read_text(errors="replace"))
        # The old rule alone would veto each one: the exemption is what clears it.
        body = (root / n).read_text()
        assert lbs.mentions_bead(body, "AestheticcNext-3bm73") and lbs._names_tip(body, TX_TIP), n
    assert lbs.review_file_vetoes(root, "AestheticcNext-3bm73", TX_TIP) == []


def test_txfbf_descriptive_no_echo_file_and_hidden_bead_echoes_still_veto(tmp_path):
    # No echo, no header field, descriptive name: the old prose rule still holds it.
    body = f"Allure follow-up: AestheticcNext-3bm73 at {TX_TIP[:10]} is FAIL (P2 lib/x.ts:1).\n"
    assert len(_vetoes(tmp_path / "n", "REVIEW_allure_followup_r9.md", body)) == 1
    # An other-branch echo beside an echo of the bead that only a lenient parse sees.
    for i, echo in enumerate((f"**REVIEWING:** goal/aestheticcnext-3bm73 @ {TX_TIP[:10]} (round 9)",
                              f"> REVIEWING: `goal/aestheticcnext-3bm73` @ `{TX_TIP[:10]}`")):
        body = f"REVIEWING: e3opf-structured @ 5acadb10c8\n{echo}\nFAIL\n"
        assert len(_vetoes(tmp_path / f"h{i}", "REVIEW_allure_followup_r9.md", body)) == 1, echo
    # An unparseable echo beside a parseable other-branch one keeps the veto.
    body = f"REVIEWING: e3opf-structured @ 5acadb10c8\nREVIEWING: the 3bm73 branch tip\n3bm73 {TX_TIP[:8]} FAIL\n"
    assert len(_vetoes(tmp_path / "u", "REVIEW_e3opf_r9.md", body)) == 1


def test_txfbf_lander_is_not_held_by_a_harness_review_quoting_the_bead(tmp_path):
    root = _reviews(tmp_path, bead="AestheticcNext-3bm73", tip=TX_TIP)
    harness = root / "REVIEW_e3opf_structured_astra-high_r3_2026-10-24.md"
    harness.write_text("REVIEWING: e3opf-structured @ 5acadb10c839b61435d08494cf085e1a2222edfe\n\nFAIL\n"
                       f"- 3bm73 r1-r6 replayed at tip `{TX_TIP[:8]}`; AestheticcNext-3bm73 parses.\n")
    gate = next(root.glob("*_gate_*"))
    os.utime(harness, (gate.stat().st_mtime + 60,) * 2)  # newer than the gate: a veto would hold it
    ok, ev = lbs.independent_pass_for("AestheticcNext-3bm73", TX_TIP, reviews_dir=root,
                                      comments_text=_comment("2099-01-01 00:00", f"PASS @{TX_TIP}"))
    assert ok is True, ev
