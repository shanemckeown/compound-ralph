"""LUCY-e3opf attempt 3: the tightened review-verdict grammar (G1-G5).

Each class reproduces one fail-open input from the independent Astra review of b944c4f
(Ops/reviews/REVIEW_e3opf_structured_astra-high_2026-10-24.md, copied to fixtures/e3opf-g5) and asserts
refusal or ESCALATE with FIXED=0, plus the real clean reviews G5 must keep accepting.
"""
import copy
import json
import unittest

import test_review_lead as base
from test_review_lead import (R1_RAISE, R1_SRC, SHA_A, SHA_B, chk, fixed_lead, parse_text, prior_block,
                              r2_file, run_named)

FIX = base.HERE / "fixtures" / "e3opf-g5"
REF = "r1 P2 lib/pay.ts:37"


def r1_seat(text):
    return f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{text}\nVERIFIED OK\n- tests\n"


def strict_block(verdict, findings=(), prior=None, **extra):
    data = {"verdict": verdict, "findings": [{"severity": s, "file_line": fl, "text": t} for s, fl, t in findings]}
    if prior is not None:
        data["prior"] = [{"ref": r, "status": st, "evidence": "red test now green at this tip"} for r, st in prior]
    data.update(extra)
    return "```review-verdict\n" + json.dumps(data) + "\n```\n"


class G3RefIdentity(unittest.TestCase):
    """Astra P2-1 (:830): one ref closing two distinct same-round findings at one location."""
    A = "P2 lib/pay.ts:37 — retry charges twice — guard"
    B = "P2 lib/pay.ts:37 — the receipt shows the wrong total — fix"

    def lead(self, srcs):
        lead = fixed_lead(srcs[0], "@D@/REVIEW_x_astra_r2_b.md")
        lead["findings"] = [dict(lead["findings"][0], id=f"F{i + 1}", source_line=s) for i, s in enumerate(srcs)]
        return lead

    def run_pair(self, sonnet_text):
        return run_named(self.lead([self.A, sonnet_text]), [
            ("REVIEW_x_astra_a.md", r1_seat(self.A), 1000),
            ("REVIEW_x_sonnet_a.md", r1_seat(sonnet_text), 1100),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2000)])

    def test_two_distinct_findings_one_ref_escalate(self):
        code, out = self.run_pair(self.B)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=2", out)
        self.assertIn("FIXED=0", out)

    def test_same_text_in_two_seats_is_one_finding(self):
        code, out = self.run_pair(self.A)
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=0", out)
        self.assertIn("FIXED=2", out)

    def test_ref_matching_no_round_n_finding_refused(self):
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")
        code, out = run_named(lead, [("REVIEW_x_astra_a.md", R1_RAISE, 1000),
                                     ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed"),
                                                                              ("r1 P2 lib/pay.ts:38", "closed")]), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("prior ref r1 P2 lib/pay.ts:38 (closed) matches no round-1 finding", out)


class G1OneTopLevelBlock(unittest.TestCase):
    """Astra P2-2 (:432) and P2-3 (:436): a nested example block, an appended second response."""

    def test_closed_prior_inside_a_four_backtick_example_is_not_authoritative(self):
        example = ("Template example:\n````markdown\n```review-verdict\n"
                   + json.dumps({"verdict": "PASS", "findings": [],
                                 "prior": [{"ref": REF, "status": "closed", "evidence": "example only, not run"}]})
                   + "\n```\n````\n")
        r2 = (f"REVIEWING: goal/x @ {SHA_B}\nPASS\n{example}VERIFIED OK\n- tests\nUNVERIFIED\n- live\n"
              + prior_block("PASS"))
        self.assertEqual(chk.file_prior("v", r2), {})
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("has no review-verdict prior entry", out)
        self.assertNotIn("FIXED=", out)

    def test_tilde_fence_nests_too(self):
        nested = "~~~~\n```review-verdict\n{\"verdict\": \"FAIL\"}\n```\n~~~~\n"
        self.assertEqual(chk.verdict_blocks("v", nested), [])

    def test_appended_same_tip_response_refused(self):
        first = r2_file(prior=[(REF, "closed")])
        second = f"REVIEWING: goal/x @ {SHA_B}\nPASS\n" + prior_block("PASS", (), [])
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_a.md", R1_RAISE, 1000),
                               ("REVIEW_x_astra_r2_b.md", first + second, 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("2 review-verdict blocks; one response per file", out)
        self.assertNotIn("FIXED=", out)

    def test_unclosed_block_or_swallowing_fence_refused(self):
        for text in (f"REVIEWING: goal/x @ {SHA_B}\nPASS\n```review-verdict\n{{\"verdict\": \"PASS\", \"findings\": []}}\n",
                     f"REVIEWING: goal/x @ {SHA_B}\nPASS\n````\nexample\n" + prior_block("PASS")):
            with self.assertRaises(ValueError, msg=text):
                parse_text(text)
            self.assertRaises(ValueError, chk.verdict_blocks, "v", text)


class G4EveryEcho(unittest.TestCase):
    """Astra P2-4 (:763): a non-newest tip seat's open prior must not drop out with its echo."""

    def test_seat_without_echo_refuses_the_run(self):
        sonnet = r2_file(prior=[(REF, "open")], evidence="retry still charges twice").split("\n", 1)[1]
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_a.md", R1_RAISE, 1000),
            ("REVIEW_x_sonnet_r2_b.md", sonnet, 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_sonnet_r2_b.md: no single well-formed REVIEWING echo", out)
        self.assertNotIn("FIXED=", out)

    def test_any_prior_in_the_run_needs_every_echo(self):
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"][0].update(verdict="consider", follow_up="Retry", reason="owned by the payments epic",
                                   category="money")
        lead["findings"][0].pop("fixed_by")
        code, out = run_named(lead, [("REVIEW_x_astra_a.md", R1_RAISE.split("\n", 1)[1], 1000),
                                     ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "open")], evidence="unchanged"),
                                      2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_a.md: no single well-formed REVIEWING echo", out)

    def test_trailing_period_after_the_sha_is_tolerated(self):
        self.assertEqual(chk.echo_of(f"REVIEWING: goal/x @ {SHA_B}.\nPASS\n"), SHA_B)


class G2ExactKeys(unittest.TestCase):
    """Astra P2-5 (:367) and its two Noted items: no aliases, no extra or null keys."""

    def parse(self, block_text):
        return parse_text(f"REVIEWING: goal/x @ {SHA_B}\nFAIL\n" + block_text)

    def test_unexpected_priority_key_refused_by_name(self):
        f = {"severity": "P2", "priority": "P1", "file_line": "lib/pay.ts:37", "text": "retry charges twice"}
        with self.assertRaisesRegex(ValueError, "unexpected keys priority; expected severity,file_line,text"):
            self.parse("```review-verdict\n" + json.dumps({"verdict": "FAIL", "findings": [f]}) + "\n```\n")

    def test_null_or_empty_file_line_refused(self):
        for fl in (None, "", "   ", 37):
            f = {"severity": "P2", "file_line": fl, "text": "lib/pay.ts:37 — retry charges twice"}
            with self.assertRaises(ValueError, msg=repr(fl)):
                self.parse("```review-verdict\n" + json.dumps({"verdict": "FAIL", "findings": [f]}) + "\n```\n")

    def test_unknown_prior_key_refused(self):
        entry = {"ref": REF, "status": "closed", "evidence": "red test green at this tip", "note": "x"}
        with self.assertRaisesRegex(ValueError, "unexpected keys note"):
            parse_text(f"REVIEWING: goal/x @ {SHA_B}\nPASS\n```review-verdict\n"
                       + json.dumps({"verdict": "PASS", "findings": [], "prior": [entry]}) + "\n```\n")

    def test_null_prior_or_top_level_extra_refused(self):
        for data in ({"verdict": "PASS", "findings": [], "prior": None},
                     {"verdict": "PASS", "findings": [], "summary": "clean"}):
            with self.assertRaises(ValueError, msg=json.dumps(data)):
                self.parse("```review-verdict\n" + json.dumps(data) + "\n```\n")

    def test_bad_shapes_never_grade(self):
        """End to end: each malformed tip review refuses the run, so nothing prints FIXED."""
        good = {"severity": "P3", "file_line": "lib/a.ts:1", "text": "copy nit"}
        for bad in (dict(good, priority="P1"), dict(good, file_line=None), {"severity": "P3", "text": "x", "loc": "y"}):
            r2 = (f"REVIEWING: goal/x @ {SHA_B}\nFAIL\n```review-verdict\n"
                  + json.dumps({"verdict": "FAIL", "findings": [bad],
                                "prior": [{"ref": REF, "status": "closed", "evidence": "red test green here"}]})
                  + "\n```\n")
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                                  [("REVIEW_x_astra_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
            self.assertEqual(code, 1, out)
            self.assertNotIn("FIXED=", out)

    def test_real_8mpih_astra_r3_names_the_unexpected_keys(self):
        with self.assertRaises(ValueError) as e:
            chk.reviewer_findings([str(FIX / "REVIEW_8mpih_astra-xhigh_r3_2026-10-24.md")])
        self.assertIn("missing keys severity,file_line,text; unexpected keys description,file,line,priority; "
                      "expected severity,file_line,text", str(e.exception))

    def test_legacy_shape_at_the_current_tip_cannot_grade(self):
        legacy = (f"REVIEWING: goal/x @ {SHA_B}\nFAIL\nP3 lib/a.ts:1 — copy nit\n```review-verdict\n"
                  + json.dumps({"verdict": "FAIL", "findings": [{"severity": "P3", "text": "lib/a.ts:1 — copy nit"}],
                                "prior": [{"ref": REF, "status": "closed", "evidence": "red test green here"}]})
                  + "\n```\n")
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")
        lead["findings"].append({"id": "F2", "source_line": "P3 lib/a.ts:1 — copy nit", "verdict": "noted",
                                 "category": "style", "reason": "copy only, owner agreed"})
        code, out = run_named(lead, [("REVIEW_x_astra_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", legacy, 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("has a finding without file_line", out)
        self.assertEqual(len(parse_text(legacy)), 1)  # a raising review in this shape still parses


class G5ProseByLocation(unittest.TestCase):
    """The block is the single source of findings; prose is cross-checked by location only."""

    def file(self, prose, findings=(), verdict=None, prior=None):
        verdict = verdict or ("FAIL" if findings else "PASS")
        return (f"REVIEWING: goal/x @ {SHA_B}\n{verdict}\n{prose}\nVERIFIED OK\n- tests\n"
                + strict_block(verdict, findings, prior))

    def test_prose_claim_with_no_block_finding_refused(self):
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at tokens.ts"):
            parse_text(self.file("P1 in tokens.ts: retry bypasses revoke", [("P2", "lib/x.ts:3", "label")]))

    def test_basename_claim_matches_the_block_file(self):
        found = parse_text(self.file("P1 in tokens.ts: retry bypasses revoke",
                                     [("P1", "lib/auth/tokens.ts:40", "retry bypasses revoke — fix")]))
        self.assertEqual(found, ["P1 lib/auth/tokens.ts:40 — retry bypasses revoke — fix"])
        with self.assertRaises(ValueError):  # '/'-anchored: kens.ts is not tokens.ts
            parse_text(self.file("P1 in kens.ts: retry bypasses revoke",
                                 [("P1", "lib/auth/tokens.ts:40", "retry bypasses revoke — fix")]))

    def test_severity_without_a_path_is_ignored(self):
        self.assertEqual(parse_text(self.file("Earlier P3 grading retained.")), [])

    def test_severity_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, "severity mismatch"):
            parse_text(self.file("P2 lib/x.ts — wrong total", [("P1", "lib/x.ts:9", "wrong total — fix")]))

    def test_claim_line_must_match(self):
        with self.assertRaises(ValueError):
            parse_text(self.file("P1 lib/x.ts:10 — wrong total", [("P1", "lib/x.ts:9", "wrong total — fix")]))

    def test_ambiguous_basename_with_different_severities_refused(self):
        two = [("P1", "lib/a/tokens.ts:1", "revoke — fix"), ("P2", "lib/b/tokens.ts:2", "copy — fix")]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            parse_text(self.file("P1 tokens.ts revoke skipped", two))
        same = [("P1", "lib/a/tokens.ts:1", "revoke — fix"), ("P1", "lib/b/tokens.ts:2", "copy — fix")]
        self.assertEqual(len(parse_text(self.file("P1 tokens.ts revoke skipped", same))), 2)

    def test_prior_ref_counts_as_a_match(self):
        text = self.file("- P2 lib/pay.ts:37 is still open at this tip", prior=[(REF, "open")])
        self.assertEqual(parse_text(text), [])

    def test_severity_in_a_code_span_is_quoted(self):
        found = parse_text(self.file("P2 lib/a.ts:3 — two seats both wrote `P2 lib/x.ts:12`",
                                     [("P2", "lib/a.ts:3", "ambiguous ref — fix")]))
        self.assertEqual(len(found), 1)

    def test_closure_note_is_not_a_claim_but_a_new_located_severity_is(self):
        self.assertEqual(parse_text(self.file("- r1 P1 lib/pay.ts:37 closed: do not populate; tests fail without it")),
                         [])
        with self.assertRaises(ValueError):
            parse_text(self.file("- r1 P1 lib/pay.ts:37 closed: fixed, but P2 lib/b.ts:9 now drops rows"))

    def test_open_note_must_be_carried_or_have_its_origin(self):
        open_note = "- r1 P2 lib/pay.ts:37 open: still charges twice on retry"
        self.assertEqual(parse_text(self.file(open_note)), [])  # parses; resolved at run level
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = []
        code, out = run_named(lead, [("REVIEW_x_astra_r2_b.md", self.file(open_note), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("note on r1 P2 lib/pay.ts:37 matches no round-1 finding", out)
        lead["findings"] = [{"id": "F1", "source_line": R1_SRC, "verdict": "act_on", "category": "money",
                             "clinic_scenario": "reception retries a declined card", "demonstrated": "seat repro"}]
        code, out = run_named(lead, [("REVIEW_x_astra_a.md", R1_RAISE, 1000),
                                     ("REVIEW_x_astra_r2_b.md", self.file(open_note), 2000)])
        self.assertEqual(code, 0, out)
        self.assertNotIn("note on", out)

    def test_prose_closures_still_never_grade(self):
        for tail in base.ASTRA_FAIL_OPEN_TAILS + ["tests pass", "regression green on both paths"]:
            r2 = self.file(f"- {REF} closed: {tail}", prior=[])
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                                  [("REVIEW_x_astra_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
            self.assertEqual(code, 1, tail)
            self.assertNotIn("FIXED=", out)


class G5RealFiles(unittest.TestCase):
    """Byte copies of the clean reviews the old text pairing refused (bead G5 comment, 24 Oct)."""

    def parse(self, name):
        chk.UNBOUND_NOTES.clear()
        chk.NOTE_REFS.clear()
        return chk.reviewer_findings([str(FIX / name)])

    def test_q2lod_cp2_seats_parse(self):
        for name, n in (("NOTES_REVIEW_q2lod_cp2_astra-xhigh-server_2026-10-24.md", 5),
                        ("NOTES_REVIEW_q2lod_cp2_astra-xhigh-app_2026-10-24.md", 4),
                        ("NOTES_REVIEW_q2lod_cp2_astra-xhigh-tests_2026-10-24.md", 2),
                        ("NOTES_REVIEW_q2lod_cp2_opus-h2h_2026-10-24.md", 7)):
            self.assertEqual(len(self.parse(name)), n, name)

    def test_8mpih_r4_opus_paraphrase_parses(self):
        found = self.parse("REVIEW_8mpih_opus-h2h_r4_2026-10-24.md")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("P3 lib/ava/unpaid-reply.ts:96 — "), found[0])

    def test_e3opf_astra_review_with_a_quoted_example_parses(self):
        self.assertEqual(len(self.parse("REVIEW_e3opf_structured_astra-high_2026-10-24.md")), 5)

    def test_q2lod_server_open_carry_line_resolves_at_run_level(self):
        """'r1 P3 tokens.ts:172 … open: … Earlier P3 grading retained.' is matched by the block when the
        finding is carried; without it the note is deferred to the run-level round-1 origin check."""
        self.parse("NOTES_REVIEW_q2lod_cp2_astra-xhigh-server_2026-10-24.md")
        self.assertFalse([n for v in chk.NOTE_REFS.values() for n in v if "tokens.ts" in n[2]])
        text = (FIX / "NOTES_REVIEW_q2lod_cp2_astra-xhigh-server_2026-10-24.md").read_text()
        data_start = text.index("```review-verdict\n") + len("```review-verdict\n")
        data = json.loads(text[data_start:text.index("\n```", data_start)])
        data["findings"] = [f for f in data["findings"] if "tokens.ts:172" not in f["text"]]
        chk.NOTE_REFS.clear()
        parse_text(text[:data_start] + json.dumps(data) + "\n```\n")
        deferred = [n for v in chk.NOTE_REFS.values() for n in v]
        self.assertEqual([(n[0], n[1], n[3]) for n in deferred if n[2].endswith("tokens.ts")], [(1, "P3", "172")])

class OwnGateRound1(unittest.TestCase):
    """Own-gate round 1 (Astra xhigh + Opus h2h on cbd9977): three fail-opens, each now refused or escalated."""

    def file(self, prose, findings=(), prior=None, sha=SHA_B):
        verdict = "FAIL" if findings else "PASS"
        return (f"REVIEWING: goal/x @ {sha}\n{verdict}\n{prose}\nVERIFIED OK\n- tests\n"
                + strict_block(verdict, findings, prior))

    def test_extensionless_and_route_group_paths_are_claims(self):
        for loc in ("Dockerfile:12", ".gcloudignore:5", ".husky/pre-push:3", "prisma/schema.prisma:9",
                    "app/(dashboard)/clients/page.tsx:12", "scripts/charge-patient:37",
                    "scripts/git-hooks/reference-transaction:179", "prisma/schema.prisma",
                    "mobile/android/app/build.gradle"):
            with self.assertRaisesRegex(ValueError, "prose finding not in the review-verdict block", msg=loc):
                parse_text(self.file(f"- **P1** {loc} — strips the fixture so the prod build dies — fix"))

    def test_words_with_slashes_or_dots_are_not_paths(self):
        for prose in ("No P2 and/or P3 items remain", "No H-P0/P1 hazards apply",
                      "Seen on localhost:3000 during the P3 check", "Earlier P3 grading retained."):
            self.assertEqual(parse_text(self.file(prose)), [], prose)

    def test_backticked_bare_severity_is_a_claim(self):
        for prose in ("- `P1` lib/auth/tokens.ts:40 — retry bypasses revoke — guard",
                      "Finding `P1`: lib/auth/tokens.ts:40 retry bypasses revoke"):
            with self.assertRaises(ValueError, msg=prose):
                parse_text(self.file(prose))
        self.assertEqual(len(parse_text(self.file("P2 lib/a.ts:3 — seats wrote `P2 lib/x.ts:12`",
                                                  [("P2", "lib/a.ts:3", "ambiguous ref — fix")]))), 1)

    def test_prose_finding_at_a_closed_prior_location_refused(self):
        for prose in ("- P2 lib/pay.ts:37 — retry still charges twice when the reader reconnects",
                      "- P2 lib/pay.ts — a second double-charge path via the webhook"):
            with self.assertRaisesRegex(ValueError, "prose contradicts it", msg=prose):
                parse_text(self.file(prose, prior=[(REF, "closed")]))

    def run_fixed(self, *r2s):
        return run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                         [("REVIEW_x_astra_a.md", R1_RAISE, 1000)] + list(r2s))

    def test_prose_open_note_in_another_seat_escalates(self):
        opus = self.file("- r1 P2 lib/pay.ts:37 open: retry still charges twice at this tip", prior=[])
        code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                   ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=1", out)
        self.assertIn("FIXED=0", out)

    def test_prose_open_note_against_own_closed_prior_never_grades(self):
        r2 = self.file("- r1 P2 lib/pay.ts:37 open: retry still charges twice on the second tap", prior=[(REF, "closed")])
        code, out = self.run_fixed(("REVIEW_x_astra_r2_b.md", r2, 2000))
        self.assertEqual(code, 1, out)
        self.assertIn("(it says open): not fixed", out)


class OwnGateRound2(unittest.TestCase):
    """Own-gate round 2 (Astra xhigh + Opus h2h on 6d4979c)."""
    file, run_fixed = OwnGateRound1.file, OwnGateRound1.run_fixed

    def refuses(self, prose, findings=(), prior=None):
        with self.assertRaisesRegex(ValueError, "prose finding not in the review-verdict block", msg=prose):
            parse_text(self.file(prose, findings, prior))

    def test_line_is_captured_for_every_path_form(self):
        self.refuses("P2 a.ts:38 — the previous patient's notes show — reset id",
                     [("P2", "lib/a.ts:37", "refresh hides the search input — keep it")])
        self.refuses("P1 Dockerfile:12 — fixture stripped", [("P1", "Dockerfile:11", "other — fix")])
        self.refuses("P1 .gcloudignore:5 — fixture stripped", [("P1", ".gcloudignore:4", "other — fix")])
        self.assertEqual(chk.line_claims("P1 build.gradle:40 x"), [("P1", "build.gradle", "40")])

    def test_parenthesised_absolute_and_bare_paths_are_claims(self):
        nit = [("P3", "lib/label.ts:4", "label typo — fix")]
        for prose in ("- P1 (lib/auth/session.ts:40) — a revoked token still opens another clinic's list",
                      "- P1 retry charges twice (lib/pay.ts:37)", "- P1 retry (lib/pay.ts:37-40, lib/refund.ts:9)",
                      "- P1 /Users/s/repo/lib/auth/session.ts:40 — revoked token", "- P1 ~/.wt/x/lib/pay.ts:37 — twice",
                      "- P1 build.gradle:40 — release signing dropped", "- P1 schema.prisma:9 — tenant column gone",
                      "- P1 the lib/pay module double-charges on retry"):
            self.refuses(prose, nit)
        found = parse_text(self.file("- P1 /Users/s/repo/lib/auth/session.ts:40 — revoked token still opens",
                                     [("P1", "lib/auth/session.ts:40", "revoked token — check version")]))
        self.assertEqual(len(found), 1)
        for prose, loc in (("- P1 ~/.wt/x/lib/pay.ts — retry charges twice", "lib/pay.ts:37"),
                           ("- P1 app/(dashboard)/clients/page.tsx:12 — shows another clinic's clients",
                            "app/(dashboard)/clients/page.tsx:12")):
            self.assertEqual(len(parse_text(self.file(prose, [("P1", loc, "leak — fix")]))), 1, prose)

    def test_slashed_words_are_still_not_paths(self):
        for prose in ("No P2 and/or P3 items remain", "Counts by P1/P2: none", "Both Opus/Astra agree on P3"):
            self.assertEqual(parse_text(self.file(prose)), [], prose)

    def test_bare_bracketed_severity_in_a_code_span_is_a_claim(self):
        for sev in ("`[P1]`", "`P1:`", "`(P1)`"):
            self.refuses(f"- {sev} lib/auth/tokens.ts:40 — retry bypasses revoke")
        self.assertEqual(len(parse_text(self.file('P2 lib/a.ts:3 — the key `priority:"P1"` is accepted',
                                                  [("P2", "lib/a.ts:3", "extra key — refuse")]))), 1)

    def test_qualified_status_word_is_open(self):
        for status in ("not fixed", "partially fixed", "still not resolved"):
            opus = self.file(f"- r1 P2 lib/pay.ts:37 {status}: retry still charges twice", [("P3", "lib/a.ts:1", "nit — x")])
            code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                       ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
            self.assertNotIn("FIXED=1", out, status)
            own = self.file(f"- r1 P2 lib/pay.ts:37 {status}: retry still charges twice", prior=[(REF, "closed")])
            code, out = self.run_fixed(("REVIEW_x_astra_r2_b.md", own, 2000))
            self.assertEqual(code, 1, status)
            self.assertNotIn("FIXED=", out)

    def test_basename_or_lineless_open_note_vetoes(self):
        for note in ("- r1 P2 pay.ts:37 open: retry still charges twice", "- r1 P2 lib/pay.ts open: retry twice",
                     "- r1 P2 lib/pay.ts:37 closed: guard added, but the P2 retry path still double-charges"):
            opus = self.file(note, prior=[])
            code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                       ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=1", out, note)
            self.assertIn("FIXED=0", out, note)

    def test_described_closed_note_stays_closed(self):
        opus = self.file("- r1 P2 lib/pay.ts:37 (retry), set by refund.ts and pay.ts:40 closed: guard green", prior=[])
        code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                   ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=0", out)
        self.assertIn("FIXED=1", out)



class OwnGateRound3(unittest.TestCase):
    """Own-gate round 3 (Opus h2h on 629785e; the Astra seat timed out)."""
    file, run_fixed = OwnGateRound1.file, OwnGateRound1.run_fixed
    NIT = [("P3", "lib/label.ts:4", "label typo — fix")]

    def test_finding_with_its_location_on_the_next_line_is_not_dropped(self):
        for prose in ("**P1 — Retry charges the card twice**\nThe retry in lib/pay.ts:37 resends with no key.",
                      "### P1: Retry charges the card twice\nFile: lib/pay.ts:37",
                      "1. **P1** Retry charges twice\n   - Location: `lib/pay.ts:37`",
                      "Finding (P1): Retry charges twice\n  lib/pay.ts:37 — no idempotency key",
                      "- P1 — another clinic's client notes are readable via the booking endpoint"):
            with self.assertRaisesRegex(ValueError, "start with P1 but name no file", msg=prose):
                parse_text(self.file(prose, self.NIT))

    def test_unlocated_line_is_fine_when_the_block_holds_the_finding(self):
        found = parse_text(self.file("### P1: Retry charges the card twice\nFile: lib/pay.ts:37",
                                     [("P1", "lib/pay.ts:37", "retry charges twice — key it")]))
        self.assertEqual(len(found), 1)

    def test_status_qualifier_ignores_the_path(self):
        src = "P2 lib/sms/unsubscribe.ts:37 — retry sends twice — guard"
        r1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{src}\nVERIFIED OK\n- tests\n"
        ref = "r1 P2 lib/sms/unsubscribe.ts:37"
        r2 = self.file(f"- {ref} closed: red test green at this tip", prior=[(ref, "closed")])
        code, out = run_named(fixed_lead(src, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_a.md", r1, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)
        opus = self.file("- r1 P2 lib/pay.ts:37 not yet fully fixed: the iPad path still charges twice", prior=[])
        code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                   ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
        self.assertIn("ESCALATE=1", out)
        self.assertIn("FIXED=0", out)


if __name__ == "__main__":
    unittest.main()
