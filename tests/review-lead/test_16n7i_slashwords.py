"""LUCY-16n7i: G5 reads prose slash-words ("user/owned/member", "and/or") as weak, never as locations.

A weak slash-word's severity is counted as unlocated against the block (never ignored), so a prose P1 can't
hide behind a slash. Strong paths (extension, :line, ~/ or /, trailing /, (group)/[param]/@, a dot-led or
known top-level first segment) keep today's location match. Replays the real m5lm7 r2 inputs that G5 refused.
"""
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import test_review_lead as base
from test_review_lead import SHA_B, chk, parse_text

FIX = base.HERE / "fixtures"
OPS = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/reviews"
# m5lm7 opus-h2h r1, line 17, verbatim: the line G5 refused on 25 Oct.
M5LM7_17 = "3. userId dropped from the user/owned/member keys: all green. This is the P3 above."


def strict_file(prose, findings=(), prior=None):
    verdict = "FAIL" if findings else "PASS"
    data = {"verdict": verdict, "findings": [{"severity": s, "file_line": fl, "text": t} for s, fl, t in findings]}
    if prior is not None:
        data["prior"] = [{"ref": r, "status": st, "evidence": "red test now green at this tip"} for r, st in prior]
    return (f"REVIEWING: goal/x @ {SHA_B}\n{verdict}\n{prose}\nVERIFIED OK\n- tests\n"
            "```review-verdict\n" + json.dumps(data) + "\n```\n")


def replay(name, scope_lock):
    """chk.main over a fixtures/replay-<name> copy of real Ops/reviews inputs, fixed_by re-pointed at the copy."""
    src = FIX / f"replay-{name}"
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        for f in src.iterdir():
            shutil.copy(f, d)
        lead = Path(d) / "lead-round-2.json"
        lead.write_text(lead.read_text().replace(OPS, d))
        argv = ["--lead", str(lead), "--out-dir", str(Path(d) / "out"), "--round", "2"]
        for f in sorted(Path(d).glob("REVIEW_*.md")):
            argv += ["--verdict", str(f)]
        if scope_lock:
            argv.append("--scope-lock")
        out = io.StringIO()
        with redirect_stderr(out), redirect_stdout(out):
            code = chk.main(argv)
        return code, out.getvalue()


def weak(token):
    return chk.weak_path(chk.repo_paths(f"P1 {token} x")[0])


class WeakOrStrong(unittest.TestCase):
    def test_prose_slash_words_are_weak(self):
        for token in ("user/owned/member", "and/or", "identity/idempotent", "read/write", "opt-in/opt-out"):
            self.assertTrue(weak(token), token)

    def test_real_paths_are_strong(self):
        for token in ("src/a.ts:12", "app/(booking)/page.tsx", "lib/auth", "services/worker", "Lib/Auth",
                      "~/x/y", "/abs/y", "docs/", ".github/workflows", "user/owned/member.ts", "users/[id]/edit",
                      "pkg/@scope/x", "mobile/src/terminal", "scripts/git-hooks/reference-transaction",
                      # own-gate r1 Astra: any dot, digit or underscore in any segment is strong
                      "reports/patient-export.sql-template", "reports/export.2", "reports/export.typescriptx",
                      "reports/v2/summary", "reports/patient_export", "Next.js/React"):
            self.assertFalse(weak(token), token)

    def test_tokens_without_a_slash_are_never_weak(self):
        for token in ("Dockerfile:3", "tokens.ts", ".gcloudignore:5"):
            m = chk.repo_paths(f"P1 {token} x")
            self.assertFalse(chk.weak_path(m[0]), token)


class G5SlashWords(unittest.TestCase):
    P3 = [("P3", "lib/auth/request-memo.ts:70", "H-D1 test gap — add a case")]

    def test_m5lm7_line_17_verbatim_passes(self):
        self.assertEqual(len(parse_text(strict_file(M5LM7_17, self.P3))), 1)

    def test_m5lm7_real_file_parses(self):
        chk.UNBOUND_NOTES.clear()
        found = chk.reviewer_findings([str(FIX / "replay-m5lm7" / "REVIEW_m5lm7_opus-h2h_r1_2026-10-25.md")])
        self.assertEqual([f[:2] for f in found], ["P3"])

    def test_and_or_passes(self):
        self.assertEqual(parse_text(strict_file("Retry and/or refund paths both checked.")), [])
        self.assertEqual(len(parse_text(strict_file("The P3 and/or its follow-up stay open.", self.P3))), 1)

    def test_weak_p1_with_no_p1_in_the_block_refuses(self):
        for prose in ("P1 user/owned/member leak", "Also a P1 user/owned/member leak on retry",
                      "- r1 P3 the retry note open: P1 user/owned/member leak"):
            with self.assertRaisesRegex(ValueError, "P1 claim\\(s\\) name only a slash-word", msg=prose):
                parse_text(strict_file(prose, self.P3))

    def test_weak_claims_count_per_severity(self):
        one = [("P1", "lib/pay.ts:3", "double charge — fix")]
        self.assertEqual(len(parse_text(strict_file("P1 user/owned/member leak", one))), 1)
        with self.assertRaisesRegex(ValueError, "2 prose P1 claim\\(s\\) name only a slash-word"):
            parse_text(strict_file("P1 user/owned leak; P1 team/member leak", one))

    def test_known_dir_slash_word_is_a_path(self):
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at services/worker"):
            parse_text(strict_file("P2 services/worker x", self.P3))

    def test_strong_paths_still_located(self):
        for prose, where in (("P1 src/a.ts:12 — leak", "src/a.ts:12"),
                             ("P1 app/(booking)/page.tsx 588/578 — leak", "app/\\(booking\\)/page.tsx"),
                             ("P1 Dockerfile:3 — fixture stripped", "Dockerfile:3")):
            with self.assertRaisesRegex(ValueError, f"no review-verdict finding or prior ref at {where}", msg=prose):
                parse_text(strict_file(prose, self.P3))
        self.assertEqual(chk.line_claims("P1 app/(booking)/page.tsx 588/578"),
                         ([("P1", "app/(booking)/page.tsx", None)], []))

    def test_a_mixed_line_binds_as_before(self):
        # own-gate r1/r2: with any strong path on the line, slash-words are locations again (as on main), so
        # they refuse; no severity borrows the strong path or escapes it.
        self.assertEqual(chk.line_claims("P1 user/owned/member leak at lib/x.ts:4"),
                         ([("P1", "user/owned/member", None)], []))
        unrelated = [("r1 P1 lib/pay.ts:37", "closed")]
        for line, where in (
                ("P2 reports/summary returns a blank export; P2 exports/archive omits the saved report; "
                 "P2 lib/label.ts:3 uses an unclear local variable", "reports/summary"),
                ("P2 lib/label.ts:3 uses an unclear local variable; P2 reports/summary returns a blank export",
                 "reports/summary"),
                ("lib/tenant.ts:90 — P1 the user/owned/member filter exposes another clinic patient export",
                 "user/owned/member")):
            with self.assertRaisesRegex(ValueError, f"no review-verdict finding or prior ref at {where}", msg=line):
                parse_text(strict_file(line, [("P2", "lib/label.ts:3", "uses an unclear local variable")],
                                       prior=unrelated))

    def test_an_earlier_note_with_a_strong_reference_binds_its_tail_as_before(self):
        # own-gate r3 Astra: the relief is per whole line, so a note's strong reference disables it for the tail.
        unrelated = [("r1 P1 lib/pay.ts:37", "closed")]
        for prose in ("- r1 P1 lib/pay.ts:37 closed: P1 reports/patient-export exposes another clinic patient export",
                      "- r1 P1 lib/pay.ts:37 closed: P1 reports/patient-export leaks; P1 team/member-list leaks"):
            with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at reports/patient-export",
                                        msg=prose):
                parse_text(strict_file(prose, prior=unrelated))
        # A note whose every path is a slash-word keeps the relief (counted, here with no P1 in the block).
        with self.assertRaisesRegex(ValueError, "P1 claim\\(s\\) name only a slash-word"):
            parse_text(strict_file("- r1 P3 the retry note open: P1 user/owned/member leak", self.P3))

    def test_a_slash_word_cut_out_of_a_longer_token_is_strong(self):
        # own-gate r2 Astra: PATH_TOKEN stops at '+', leaving 'reports/patient'; it is not a whole prose word.
        for token in ("reports/patient+export.sql-template", "reports/patient%20export", "x=reports/summary",
                      "reports/summary#anchor"):
            m = [x for x in chk.repo_paths(f"P1 {token} leak") if x.group(1).startswith("reports/")]
            self.assertTrue(m and not chk.weak_path(m[0]), token)
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at reports/patient"):
            parse_text(strict_file("P1 reports/patient+export.sql-template exposes another clinic patient export",
                                   prior=[("r1 P1 lib/pay.ts:37", "closed")]))
        for prose in ("P3 user/owned/member.", "(P3 user/owned/member)", "P3 'and/or', P3 \"read/write\":"):
            self.assertTrue(all(chk.weak_path(m) for m in chk.repo_paths(prose)), prose)

    def test_an_unrelated_prior_does_not_cover_a_dotted_path(self):
        # own-gate r1 Astra: an extension of any shape is strong, so the claim must match the block by location.
        for loc in ("reports/patient-export.sql-template", "reports/export.2"):
            with self.assertRaisesRegex(ValueError, f"no review-verdict finding or prior ref at {loc}", msg=loc):
                parse_text(strict_file(f"P1 {loc} exposes another clinic patient export",
                                       prior=[("r1 P1 lib/pay.ts:37", "closed")]))


class WeakClaimsNeedBlockFindings(unittest.TestCase):
    """Attempt 2 (W1/W2): the independent r1 repros. A weak claim may be a real extensionless path, so it is
    counted against block findings only, one each; a prior ref, however unrelated or closed, never covers it."""
    SLASH = "claim\\(s\\) name only a slash-word"

    def test_an_unrelated_closed_prior_does_not_cover_a_weak_p1(self):
        # independent r1 P1 (:901): 'workers/billing' is a real path written as plain words.
        with self.assertRaisesRegex(ValueError, f"1 prose P1 {self.SLASH}"):
            parse_text(strict_file("P1 workers/billing double-charges on retry",
                                   prior=[("r1 P1 lib/pay.ts:37", "closed")]))

    def test_a_weak_note_reference_absorbs_nothing(self):
        # independent r1 P2 (:974): the note's weak reference is not a reference; every P1 on it is a weak claim.
        prose = "- r1 P1 reports/summary closed: P1 exports/archive leaks; P1 team/member-list leaks"
        with self.assertRaisesRegex(ValueError, f"3 prose P1 {self.SLASH}"):
            parse_text(strict_file(prose, prior=[("r1 P1 reports/summary:10", "closed")]))
        # The note's own severity counts too, so an open note on a slash-word can't drop out.
        with self.assertRaisesRegex(ValueError, f"1 prose P1 {self.SLASH}"):
            parse_text(strict_file("- r1 P1 workers/billing open: still double-charges",
                                   prior=[("r1 P1 workers/billing:10", "closed")]))

    def test_the_r3_note_tail_case_still_refuses(self):
        # own-gate r3: a strong note reference binds the tail as on main (a location refusal, not a count).
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at reports/patient-export"):
            parse_text(strict_file("- r1 P1 lib/pay.ts:37 closed: P1 reports/patient-export leaks",
                                   prior=[("r1 P1 lib/pay.ts:37", "closed")]))

    def test_one_block_finding_per_weak_claim(self):
        one = [("P1", "lib/pay.ts:3", "double charge — fix")]
        self.assertEqual(len(parse_text(strict_file("P1 workers/billing double-charges", one))), 1)
        with self.assertRaisesRegex(ValueError, f"2 prose P1 {self.SLASH}"):
            parse_text(strict_file("P1 workers/billing double-charges\nP1 team/member-list leaks", one,
                                   prior=[("r1 P1 lib/old.ts:9", "closed")]))

    def test_weak_claims_and_fileless_lines_share_one_pool(self):
        # A weak claim and a line naming no file can't both lean on one block finding.
        one = [("P1", "lib/pay.ts:3", "double charge — fix")]
        with self.assertRaisesRegex(ValueError, "2 prose line\\(s\\) start with P1 but name no file"):
            parse_text(strict_file("P1 workers/billing double-charges\nP1 the wrapped finding\n", one))

    def test_a_block_finding_another_line_names_does_not_cover_a_weak_p1_or_p2(self):
        # own-gate a2 r1 Astra: the block's one P1 belongs to the located face-map line; the prose
        # workers/billing P1 is a second finding and must not lean on it. Same shape at P2.
        for sev in ("P1", "P2"):
            one = [(sev, "lib/face-map.ts:37", "two devices editing the same map overwrite each other — merge")]
            prose = (f"{sev} lib/face-map.ts:37 — two devices editing the same map overwrite each other — merge\n"
                     f"{sev} workers/billing double-charges when reception retries a terminal payment")
            with self.assertRaisesRegex(ValueError, f"1 prose {sev} {self.SLASH}", msg=sev):
                parse_text(strict_file(prose, one))
            # A located line in other words names the finding too (the first line above is verbatim).
            reworded = f"{sev} lib/face-map.ts:37 strokes are lost\n{sev} workers/billing double-charges"
            with self.assertRaisesRegex(ValueError, f"1 prose {sev} {self.SLASH}", msg=sev):
                parse_text(strict_file(reworded, one))
            # An unnamed block finding still covers it.
            self.assertEqual(len(parse_text(strict_file(f"{sev} workers/billing double-charges", one))), 1)

    def test_a_weak_p3_may_point_back_at_a_named_p3(self):
        # The m5lm7 shape: a located P3 line, then a slash-word line pointing back at it.
        p3 = [("P3", "lib/auth/request-memo.ts:70", "H-D1 test gap — add a case")]
        prose = "P3 lib/auth/request-memo.ts:70 — H-D1 test gap\n" + M5LM7_17
        self.assertEqual(len(parse_text(strict_file(prose, p3))), 1)

    def test_a_weak_note_with_a_finding_in_the_block_passes(self):
        p3 = [("P3", "lib/auth/request-memo.ts:70", "H-D1 test gap — add a case")]
        self.assertEqual(len(parse_text(strict_file("- r1 P3 user/owned/member open: still a gap", p3))), 1)


class ReplayM5lm7(unittest.TestCase):
    def test_r2_inputs_no_longer_refused(self):
        code, out = replay("m5lm7", scope_lock=False)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)


if __name__ == "__main__":
    unittest.main()
