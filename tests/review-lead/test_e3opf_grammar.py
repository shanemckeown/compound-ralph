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

    def run_pair(self, sonnet_text, opus_prior=None):
        """r1: two seats at one location. r2: an astra seat closing it, plus an opus seat when opus_prior is
        given ([] = a block with no entry for it)."""
        opus = [] if opus_prior is None else [("REVIEW_x_opus_r2_b.md", r2_file(prior=opus_prior), 2100)]
        return run_named(self.lead([self.A, sonnet_text]), [
            ("REVIEW_x_astra_r1_a.md", r1_seat(self.A), 1000),
            ("REVIEW_x_sonnet_r1_a.md", r1_seat(sonnet_text), 1100),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2000), *opus])

    def test_two_distinct_findings_one_ref_escalate(self):
        # LUCY-unykv: a newest-round seat with no closed entry for the ambiguous ref keeps the escalation.
        code, out = self.run_pair(self.B, opus_prior=[])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=2", out)
        self.assertIn("FIXED=0", out)
        # Every newest-round seat (here the only one) closes the location with evidence: both count fixed.
        code, out = self.run_pair(self.B)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=2", out)

    def test_same_text_in_two_seats_is_one_finding(self):
        code, out = self.run_pair(self.A)
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=0", out)
        self.assertIn("FIXED=2", out)

    def test_ref_matching_no_round_n_finding_refused(self):
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
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
                              [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("has no review-verdict prior entry", out)
        self.assertNotIn("FIXED=", out)

    def test_tilde_fence_nests_too(self):
        nested = "~~~~\n```review-verdict\n{\"verdict\": \"FAIL\"}\n```\n~~~~\n"
        with self.assertRaisesRegex(ValueError, "only review-verdict block sits inside another fence"):
            chk.verdict_blocks("v", nested)
        self.assertEqual(len(chk.verdict_blocks("v", nested + prior_block("PASS"))), 1)

    def test_appended_same_tip_response_refused(self):
        first = r2_file(prior=[(REF, "closed")])
        second = f"REVIEWING: goal/x @ {SHA_B}\nPASS\n" + prior_block("PASS", (), [])
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
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
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
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
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE.split("\n", 1)[1], 1000),
                                     ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "open")], evidence="unchanged"),
                                      2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_r1_a.md: no single well-formed REVIEWING echo", out)

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
                                  [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
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
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", legacy, 2000)])
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

    def test_severity_in_a_code_span_is_a_claim(self):
        """LUCY-e3opf B2: code spans are read like plain text, so a quoted example is a claim too."""
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at lib/x.ts:12"):
            parse_text(self.file("P2 lib/a.ts:3 — two seats both wrote `P2 lib/x.ts:12`",
                                 [("P2", "lib/a.ts:3", "ambiguous ref — fix")]))

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
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                                     ("REVIEW_x_astra_r2_b.md", self.file(open_note), 2000)])
        self.assertEqual(code, 0, out)
        self.assertNotIn("note on", out)

    def test_prose_closures_still_never_grade(self):
        for tail in base.ASTRA_FAIL_OPEN_TAILS + ["tests pass", "regression green on both paths"]:
            r2 = self.file(f"- {REF} closed: {tail}", prior=[])
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                                  [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
            self.assertEqual(code, 1, tail)
            self.assertNotIn("FIXED=", out)


class G5RealFiles(unittest.TestCase):
    """Byte copies of the clean reviews the old text pairing refused (bead G5 comment, 24 Oct)."""

    def parse(self, name):
        chk.UNBOUND_NOTES.clear()
        chk.NOTE_REFS.clear()
        return chk.reviewer_findings([str(FIX / name)])

    def server_text(self):
        """The q2lod cp2 server seat with 'identity/idempotent' reworded (B2 reads it as a path)."""
        text = (FIX / "NOTES_REVIEW_q2lod_cp2_astra-xhigh-server_2026-10-24.md").read_text()
        assert "identity/idempotent" in text
        return text.replace("identity/idempotent", "identity, idempotent")

    def test_q2lod_cp2_seats_parse(self):
        # B2: the server seat's carry line says 'identity/idempotent' next to its P3, a slash token, so the
        # real file is refused (an accepted false refusal, listed on the bead); reworded it parses.
        with self.assertRaisesRegex(ValueError, "prior ref at identity/idempotent"):
            self.parse("NOTES_REVIEW_q2lod_cp2_astra-xhigh-server_2026-10-24.md")
        self.assertEqual(len(parse_text(self.server_text())), 5)
        for name, n in (("NOTES_REVIEW_q2lod_cp2_astra-xhigh-app_2026-10-24.md", 4),
                        ("NOTES_REVIEW_q2lod_cp2_astra-xhigh-tests_2026-10-24.md", 2),
                        ("NOTES_REVIEW_q2lod_cp2_opus-h2h_2026-10-24.md", 7)):
            self.assertEqual(len(self.parse(name)), n, name)

    def test_8mpih_r4_opus_paraphrase_parses(self):
        found = self.parse("REVIEW_8mpih_opus-h2h_r4_2026-10-24.md")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("P3 lib/ava/unpaid-reply.ts:96 — "), found[0])

    def test_e3opf_astra_review_with_a_quoted_example_is_refused(self):
        """B2: its prose quotes `r1 P2 lib/x.ts:12` as an example; read as plain text that is an unmatched
        claim (the accepted false refusal of a frozen prose sweep)."""
        with self.assertRaisesRegex(ValueError, "no review-verdict finding or prior ref at lib/x.ts:12"):
            self.parse("REVIEW_e3opf_structured_astra-high_2026-10-24.md")

    def test_q2lod_server_open_carry_line_resolves_at_run_level(self):
        """'r1 P3 tokens.ts:172 … open: … Earlier P3 grading retained.' is matched by the block when the
        finding is carried; without it the note is deferred to the run-level round-1 origin check."""
        chk.NOTE_REFS.clear()
        parse_text(self.server_text())
        # B1: a note's status word is never read, so its closed r1 P1 :132 note (not carried in the block)
        # needs its round-1 origin like any other; the carried :172 note does not.
        self.assertEqual([(n[0], n[1], n[3]) for v in chk.NOTE_REFS.values() for n in v if "tokens.ts" in n[2]],
                         [(1, "P1", "132")])
        text = self.server_text()
        data_start = text.index("```review-verdict\n") + len("```review-verdict\n")
        data = json.loads(text[data_start:text.index("\n```", data_start)])
        data["findings"] = [f for f in data["findings"] if "tokens.ts:172" not in f["text"]]
        chk.NOTE_REFS.clear()
        parse_text(text[:data_start] + json.dumps(data) + "\n```\n")
        deferred = [n for v in chk.NOTE_REFS.values() for n in v]
        self.assertEqual(sorted((n[0], n[1], n[3]) for n in deferred if n[2].endswith("tokens.ts")),
                         [(1, "P1", "132"), (1, "P3", "172")])

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
        for prose in ("No H-P0/P1 hazards apply", "Seen on localhost:3000 during the P3 check",
                      "Earlier P3 grading retained."):
            self.assertEqual(parse_text(self.file(prose)), [], prose)
        # LUCY-e3opf B2: any slash token is a path, so 'and/or' next to a severity is a claim (an accepted
        # false refusal: it fails closed).
        with self.assertRaisesRegex(ValueError, "prior ref at and/or"):
            parse_text(self.file("No P2 and/or P3 items remain"))

    def test_backticked_bare_severity_is_a_claim(self):
        for prose in ("- `P1` lib/auth/tokens.ts:40 — retry bypasses revoke — guard",
                      "Finding `P1`: lib/auth/tokens.ts:40 retry bypasses revoke"):
            with self.assertRaises(ValueError, msg=prose):
                parse_text(self.file(prose))
        with self.assertRaises(ValueError):  # B2: a code span is plain text
            parse_text(self.file("P2 lib/a.ts:3 — seats wrote `P2 lib/x.ts:12`",
                                 [("P2", "lib/a.ts:3", "ambiguous ref — fix")]))

    def test_prose_finding_at_a_closed_prior_location_refused(self):
        for prose in ("- P2 lib/pay.ts:37 — retry still charges twice when the reader reconnects",
                      "- P2 lib/pay.ts — a second double-charge path via the webhook"):
            with self.assertRaisesRegex(ValueError, "prose contradicts it", msg=prose):
                parse_text(self.file(prose, prior=[(REF, "closed")]))

    def run_fixed(self, *r2s):
        return run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                         [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000)] + list(r2s))

    def test_prose_open_note_in_a_seat_without_prior_is_not_fixed(self):
        """LUCY-e3opf B1: the seat has no prior entry for the ref, so it stays NOT fixed (not a dispute)."""
        opus = self.file("- r1 P2 lib/pay.ts:37 open: retry still charges twice at this tip", prior=[])
        code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                   ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        self.assertIn("FIXED=0", out)

    def test_block_wins_over_its_own_prose_note(self):
        """B1: a seat's block is authoritative over its own prose; the status word in prose is never read."""
        r2 = self.file("- r1 P2 lib/pay.ts:37 open: retry still charges twice on the second tap", prior=[(REF, "closed")])
        code, out = self.run_fixed(("REVIEW_x_astra_r2_b.md", r2, 2000))
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)


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

    def test_tallies_are_not_paths_but_slashed_words_are(self):
        for prose in ("Counts by P1/P2: none", "Counts by P1/P2/P3: none.", "No P3 in 14/14 suites"):
            self.assertEqual(parse_text(self.file(prose)), [], prose)
        for prose in ("No P2 and/or P3 items remain", "Both Opus/Astra agree on P3"):  # B2: fails closed
            with self.assertRaises(ValueError, msg=prose):
                parse_text(self.file(prose))

    def test_bare_bracketed_severity_in_a_code_span_is_a_claim(self):
        for sev in ("`[P1]`", "`P1:`", "`(P1)`"):
            self.refuses(f"- {sev} lib/auth/tokens.ts:40 — retry bypasses revoke")
        with self.assertRaisesRegex(ValueError, "severity mismatch"):  # B2: a code span is plain text
            parse_text(self.file('P2 lib/a.ts:3 — the key `priority:"P1"` is accepted',
                                 [("P2", "lib/a.ts:3", "extra key — refuse")]))

    def test_status_word_in_prose_is_never_read(self):
        """B1: '(not) fixed:' and friends no longer matter. A seat with no prior leaves it not fixed whatever
        its prose says; a seat that closes it in its block closes it whatever its prose says."""
        for status in ("not fixed", "partially fixed", "still not resolved", "(not) fixed", "fixed"):
            opus = self.file(f"- r1 P2 lib/pay.ts:37 {status}: retry still charges twice", [("P3", "lib/a.ts:1", "nit — x")])
            code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                       ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
            self.assertEqual(code, 1, status)  # opus raises a P3 at the tip that the lead doesn't cover
            opus = self.file(f"- r1 P2 lib/pay.ts:37 {status}: retry still charges twice", prior=[])
            code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                       ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
            self.assertIn("BLOCKING=1", out, status)
            self.assertIn("FIXED=0", out, status)

    def test_any_note_in_a_seat_without_prior_leaves_it_not_fixed(self):
        """B1: open, closed or described, a prose note is not a prior entry."""
        for note in ("- r1 P2 pay.ts:37 open: retry still charges twice", "- r1 P2 lib/pay.ts open: retry twice",
                     "- r1 P2 lib/pay.ts:37 closed: guard added, but the P2 retry path still double-charges",
                     "- r1 P2 lib/pay.ts:37 (retry), set by refund.ts and pay.ts:40 closed: guard green"):
            opus = self.file(note, prior=[])
            code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                       ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKING=1 ESCALATE=0", out, note)
            self.assertIn("FIXED=0", out, note)



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
                              [("REVIEW_x_astra_r1_a.md", r1, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)
        opus = self.file("- r1 P2 lib/pay.ts:37 not yet fully fixed: the iPad path still charges twice", prior=[])
        code, out = self.run_fixed(("REVIEW_x_opus_r2_b.md", opus, 2000),
                                   ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100))
        self.assertIn("BLOCKING=1", out)  # B1: no prior entry in the opus seat
        self.assertIn("FIXED=0", out)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------
# LUCY-e3opf attempt 4: the nine P2s of the independent Astra review of 5acadb1
# (Ops/reviews/REVIEW_e3opf_structured_astra-high_r3_2026-10-24.md), each with its exact input.
def run_one_r2(r2, lead=None, extra=()):
    """R1_RAISE at SHA_A plus one round-2 file at SHA_B, fixed_by that file unless a lead is given."""
    return run_named(lead or fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                     [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000), *extra])


def closing_r2(block_text=None, head="", tail=""):
    """A round-2 PASS at SHA_B whose block closes REF."""
    block_text = block_text if block_text is not None else strict_block("PASS", prior=[(REF, "closed")])
    return f"{head}REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n- tests\n{tail}{block_text}"


class A1CommonMarkTopLevel(unittest.TestCase):
    """:155 — a block inside an HTML comment, or indented 4 spaces / a tab, is not authoritative. Own-gate r1
    (Astra :166/:170, Opus :166): such a block is REFUSED, never silently dropped to the prose parser."""

    def assert_refused(self, r2):
        with self.assertRaisesRegex(ValueError, "review-verdict block is not top-level"):
            chk.verdict_blocks("v", r2)
        code, out = run_one_r2(r2)
        self.assertEqual(code, 1, out)
        self.assertIn("not top-level", out)
        self.assertNotIn("FIXED=", out)

    def test_html_comment_or_indented_block_is_refused(self):
        blk = strict_block("PASS", prior=[(REF, "closed")])
        indent = lambda pre: "".join(pre + ln + "\n" for ln in blk.splitlines())
        for wrapped in ("<!--\n" + blk + "-->\n", "<!-- " + blk + "-->\n", indent("    "), indent("\t"),
                        indent("   "), "<div><!--\n" + blk + "-->\n</div>\n", "<div>\n" + blk + "</div>\n",
                        "<pre>\n\n" + blk + "</pre>\n", "<script>\n\n" + blk + "</script>\n",
                        "<textarea x>\n\n" + blk + "</textarea>\n", "<!-- a --> <!-- b\n" + blk + "-->\n",
                        "<!--\na -->  <!-- b\n\n" + blk + "-->\n",
                        "- " + "`" * 4 + "markdown\n" + indent("  ") + "  " + "`" * 4 + "\n",
                        "- item\n" + indent("    "), "> " + blk.replace("\n", "\n> ")):
            self.assert_refused(closing_r2(wrapped))

    def test_raising_block_with_a_p1_only_in_the_block_is_not_lost(self):
        """Opus :166: the P2 is in prose, the P1 only in an indented/HTML-hidden block. Refused, never FIXED=1."""
        blk = strict_block("FAIL", [("P2", "lib/pay.ts:37", "retry charges twice — guard"),
                                    ("P1", "lib/refund.ts:9", "refund goes to another clinic — scope it")])
        for hidden in ("- list\n" + "".join("    " + ln + "\n" for ln in blk.splitlines()), "<!--\n" + blk):
            r1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{R1_SRC}\nVERIFIED OK\n- tests\n{hidden}"
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                                  [("REVIEW_x_astra_r1_a.md", r1, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000)])
            self.assertEqual(code, 1, out)
            self.assertNotIn("FIXED=", out)

    def test_raising_block_only_inside_another_fence_is_refused(self):
        """Opus own-gate r2 :160: a reviewer wraps its whole answer in a fence, so the block holding the P1 is
        nested and the file has no top-level block. Refused, never dropped to the prose parser."""
        blk = strict_block("FAIL", [("P2", "lib/pay.ts:37", "retry charges twice — guard"),
                                    ("P1", "lib/refund.ts:9", "refund goes to another clinic — scope it")])
        for wrap in ("`" * 4 + "markdown\n" + blk + "`" * 4 + "\n", "```text\nanswer follows\n" + blk + "```\n"):
            r1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{R1_SRC}\nVERIFIED OK\n- tests\n{wrap}"
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                                  [("REVIEW_x_astra_r1_a.md", r1, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000)])
            self.assertEqual(code, 1, out)
            self.assertIn("inside another fence", out)
            self.assertNotIn("FIXED=", out)

    def test_column_zero_block_after_closed_html_still_counts(self):
        for r2 in (closing_r2(head="<!-- reviewer scratch -->\n"), closing_r2(tail="<!--\nscratch notes\n-->\n"),
                   closing_r2(tail="<details>\nraw\n</details>\n\n"), closing_r2(tail="quoted `<!--` in prose\n"),
                   closing_r2(head="notes <!-- scratch\n"), closing_r2(head="<pre>\nraw\n</pre>\n"),
                   closing_r2(head="<!-- a --> <!-- b --> c\n")):
            code, out = run_one_r2(r2)
            self.assertEqual(code, 0, out)
            self.assertIn("FIXED=1", out)


class A2OneEchoLine(unittest.TestCase):
    """:891 — two identical REVIEWING echoes before one closing block."""

    def test_echo_split_across_lines_refused(self):
        """Own-gate r1 Astra :892: the echo must be one physical line."""
        r2 = closing_r2().replace("goal/x @ ", "goal/x @\n")
        self.assertEqual(chk.echo_of(r2), "")
        code, out = run_one_r2(r2)
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=", out)

    def test_two_identical_echoes_refused(self):
        r2 = f"REVIEWING: goal/x @ {SHA_B}\n" + closing_r2()
        self.assertEqual(chk.echo_of(r2), "")
        code, out = run_one_r2(r2)
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_r2_b.md: no single well-formed REVIEWING echo", out)
        self.assertNotIn("FIXED=", out)


class A3EchoUnconditional(unittest.TestCase):
    """:950 — a PASS block with no echo, no findings and no priors."""

    def test_pass_without_echo_refused(self):
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = []
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", "PASS\nVERIFIED OK\n- tests\n" + strict_block("PASS"), 1000)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_r1_a.md: no single well-formed REVIEWING echo", out)
        self.assertNotIn("BLOCKING=", out)


class A4StrictAtTheTip(unittest.TestCase):
    """:1044 — a current-tip legacy {severity, text} finding with a valid dismissal."""

    def test_legacy_finding_at_the_tip_refused_whatever_the_grade(self):
        legacy = (f"REVIEWING: goal/x @ {SHA_B}\nFAIL\nP3 lib/a.ts:1 — copy nit\n```review-verdict\n"
                  + json.dumps({"verdict": "FAIL", "findings": [{"severity": "P3", "text": "lib/a.ts:1 — copy nit"}]})
                  + "\n```\n")
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = [{"id": "F1", "source_line": "P3 lib/a.ts:1 — copy nit", "verdict": "dismissed",
                             "category": "style", "reason": "copy only, the owner agreed the wording"}]
        code, out = run_named(lead, [("REVIEW_x_astra_r2_b.md", legacy, 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("has a finding without file_line", out)
        self.assertNotIn("DISMISSED=", out)


class A5AmbiguityWhateverTheGrade(unittest.TestCase):
    """:1062 — two distinct round-1 findings sharing a ref, one closed prior, both dismissed."""
    A, B = G3RefIdentity.A, G3RefIdentity.B
    C = "P2 lib/label.ts:4 — the save button label reads Submit — rename"
    D = "P2 lib/label.ts:4 — the tooltip overlaps the field — move it"

    def test_dismissing_both_still_escalates(self):
        ref = "r1 P2 lib/label.ts:4"
        for grade in ("dismissed", "noted", "consider"):
            lead = fixed_lead(self.A, "x")
            lead["findings"] = [{"id": f"F{i + 1}", "source_line": s, "verdict": grade, "category": "other",
                                 "reason": "reviewer agreed it is cosmetic only", "follow_up": "tidy",
                                 "ordinary_use": "reception opens the client form"} for i, s in enumerate((self.C, self.D))]
            code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", r1_seat(self.C), 1000),
                                         ("REVIEW_x_sonnet_r1_a.md", r1_seat(self.D), 1100),
                                         ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(ref, "closed")]), 2000)])
            if grade == "noted":  # only a P3 may be Noted: refused for that, never passed
                self.assertEqual(code, 1, out)
                continue
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=2", out, grade)
            self.assertIn("DISMISSED=0", out, grade)
            self.assertIn("FOLLOWUPS=0", out, grade)
            self.assertIn("FIXED=0", out, grade)

    def test_fixed_grade_still_escalates(self):
        # LUCY-unykv: escalates unless every newest-round seat closes it; one seat keeping it open does.
        code, out = G3RefIdentity().run_pair(self.B, opus_prior=[(REF, "open")])
        self.assertIn("ESCALATE=2", out)
        self.assertIn("FIXED=0", out)


class B1EverySeatPrior(unittest.TestCase):
    """:735 — prose dissent is replaced by a structured rule: every tip review must close it."""

    def run_two(self, opus, astra=None):
        return run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_opus_r2_b.md", opus, 2000),
            ("REVIEW_x_astra_r2_b.md", astra or r2_file(prior=[(REF, "closed")]), 2100)])

    def test_prose_dissent_without_a_prior_is_not_fixed(self):
        for note in ("- r1 P2 lib/pay.ts:37 is still open", "- r1 P2 lib/pay.ts:37 (not) fixed: the iPad path"):
            opus = closing_r2(strict_block("PASS", prior=[]), tail=note + "\n")
            code, out = self.run_two(opus)
            self.assertNotIn("FIXED=1", out, note)
            if code == 0:
                self.assertIn("BLOCKING=1", out, note)
                self.assertIn("FIXED=0", out, note)

    def test_seat_without_prior_is_named_in_the_builder_input(self):
        out_dir = base.Path(base.tempfile.mkdtemp(dir="/private/tmp"))
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_opus_r2_b.md", closing_r2(strict_block("PASS", prior=[])), 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)], extra=["--out-dir", str(out_dir)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        actions = (out_dir / "lead-actions-round-2.txt").read_text()
        self.assertIn("NOT FIXED: seat REVIEW_x_opus_r2_b.md has no closed prior for r1 P2 lib/pay.ts:37", actions)

    def test_open_entry_escalates_and_every_close_fixes(self):
        code, out = self.run_two(closing_r2(strict_block("PASS", prior=[(REF, "open")])))
        self.assertIn("ESCALATE=1", out)
        self.assertIn("FIXED=0", out)
        code, out = self.run_two(closing_r2())
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_blockless_tip_review_is_a_silent_seat(self):
        """Opus own-gate r2 :1087: a tip review that forgot its block (and keeps the finding open in prose) is
        still a seat: it closes nothing, so another seat's closed prior cannot grade the finding fixed."""
        blockless = (f"REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n- tests\nUNVERIFIED\n"
                     "- r1 pay.ts:37 open: retry still charges twice at this tip\n")
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_opus_r2_b.md", blockless, 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=0", out)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertIn("FIXED=1", out)

    def test_mutants_file_is_not_a_seat(self):
        """Own-gate r1 Opus :1071: the builder's mutants file (echo-free since D1) has no block; it is not a seat,
        so it cannot deadlock every fixed grade. A seat WITH a block and no entry still blocks."""
        mutants = "PASS\n- KILLED retry guard reverted — pay suite red\n"  # echo-free (D1)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("mutants-round-2.txt", mutants, 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)


class B2WholeResponseSweep(unittest.TestCase):
    """:596, :640, :664 — the claim sweep covers the whole file, every path form and code spans."""

    def test_each_unmatched_claim_refuses_and_never_grades(self):
        for head, tail in (("P2 lib/lost.ts:9 — omitted bug\n", ""),
                           ("", "There is P2 in services/worker — still broken\n"),
                           ("", "There is P2 in schema.prisma — still broken\n"),
                           ("", "- `P2 lib/lost.ts:9` — still broken\n")):
            r2 = closing_r2(head=head, tail=tail)
            with self.assertRaisesRegex(ValueError, "prose finding not in the review-verdict block", msg=head + tail):
                parse_text(r2)
            code, out = run_one_r2(r2)
            self.assertEqual(code, 1, out)
            self.assertNotIn("FIXED=", out)

    def test_tallies_are_not_paths(self):
        for prose in ("Findings: none at P1/P2. No new P3.", "Suites 14/14 green, no P2.",
                      "H-U1/H-U3 answered; no P3."):
            self.assertEqual(parse_text(closing_r2(tail=prose + "\n")), [], prose)
        # A real path that merely contains a tally-like run is still a path.
        for prose in ("P2 in lib/2024/10/report.ts — totals drift", "P2 in reports/P1/P2/summary — wrong"):
            with self.assertRaises(ValueError, msg=prose):
                parse_text(closing_r2(tail=prose + "\n"))


# ---------------------------------------------------------------------------------------------
# LUCY-e3opf attempt 5: the four structural P2s of the independent Astra review of 0ceab2b
# (Ops/reviews/REVIEW_e3opf_structured_astra-high_r4_2026-10-24.md) and its 3bm73 r7 simulation, C1-C6.
SHA_C = "c" * 40


class C1NewlineOnly(unittest.TestCase):
    """:188 — text + VT / FF / U+2028 before the opener: splitlines() made it a line start; CommonMark doesn't."""

    def test_opener_after_a_non_newline_break_is_not_a_block(self):
        blk = strict_block("PASS", prior=[(REF, "closed")])
        for ch in ("\x0b", "\x0c", " ", " ", "\x85", "\x1c"):
            r2 = closing_r2("text" + ch + blk)
            self.assertEqual(chk.verdict_blocks("v", r2), [], repr(ch))
            code, out = run_one_r2(r2)
            self.assertNotIn("FIXED=1", out, repr(ch))
        code, out = run_one_r2(closing_r2("text\n" + blk))  # control: a real newline
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_lines_of_splits_on_newline_only(self):
        self.assertEqual(chk.lines_of("a\x0bb c\r\nd\n"), ["a\x0bb c", "d"])
        self.assertEqual("".join(chk.lines_of("a\x0cb\nc", keepends=True)), "a\x0cb\nc")


class C2BuilderEvidence(unittest.TestCase):
    """:1115 — seat-ness from content: a review under a builder-evidence name is refused, and builder evidence
    is never fixed_by."""

    def test_review_renamed_to_the_mutants_file_refused(self):
        silent = closing_r2(strict_block("PASS", prior=[]))  # Astra's repro: the strict seat with findings:[]
        for name in ("mutants-round-2.txt", "qa-output-round-2.log"):
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
                ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), (name, silent, 2000),
                ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
            self.assertEqual(code, 1, out)
            self.assertIn(f"{name}: review content in a builder-evidence file", out)
            self.assertNotIn("FIXED=", out)
        # Under its own review name it is a seat with no entry: not fixed.
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_opus_r2_b.md", silent, 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=0", out)

    def test_mutants_file_as_fixed_by_refused(self):
        mutants = "PASS\n- KILLED retry guard reverted — pay suite red\n"  # echo-free (D1)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/mutants-round-2.txt"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("mutants-round-2.txt", mutants, 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertEqual(code, 1, out)
        self.assertIn("fixed_by mutants-round-2.txt is builder evidence", out)
        self.assertNotIn("FIXED=", out)


class C3RoundFromTheName(unittest.TestCase):
    """:325 — native workflow names map to their own rounds; an unnamed file in a multi-input run is refused;
    the mtime never orders reviews (folds LUCY-888ru)."""

    def native(self, r2_mtime=3000, r3_mtime=2000):
        r3_open = closing_r2(strict_block("PASS", prior=[(REF, "open")])).replace(SHA_B, SHA_C)
        return run_named(fixed_lead(R1_SRC, "@D@/astra-verdict-round-2.log"), [
            ("astra-verdict-round-1.log", R1_RAISE, 1000), ("astra-verdict-round-2.log", closing_r2(), r2_mtime),
            ("opus-h2h-round-3.md", r3_open, r3_mtime)])

    def test_astra_native_names_repro_never_fixed(self):
        for mt in ((3000, 2000), (2000, 3000)):  # Astra's copied mtime, and the honest one: same answer
            code, out = self.native(*mt)
            self.assertEqual(code, 1, out)
            self.assertIn(f"fixed_by astra-verdict-round-2.log reviewed {SHA_B[:10]}, not the current tip {SHA_C[:10]}",
                          out)
            self.assertNotIn("FIXED=", out)

    def test_native_names_grade_in_order(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/astra-verdict-round-2.log"), [
            ("astra-verdict-round-1.log", R1_RAISE, 5000), ("astra-verdict-round-2.log", closing_r2(), 1000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_unnamed_file_in_a_multi_input_run_refused(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_a.md: the file name states no round", out)
        self.assertNotIn("FIXED=", out)

    def test_newest_round_on_two_commits_refused(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000),
            ("REVIEW_x_opus_r2_b.md", closing_r2(strict_block("PASS", prior=[(REF, "open")])).replace(SHA_B, SHA_C), 1500)])
        self.assertEqual(code, 1, out)
        self.assertIn("the newest round 2 echoes different commits", out)
        self.assertNotIn("FIXED=", out)


class C4AmbiguousActOnEscalates(unittest.TestCase):
    """:1281 — two distinct findings share an ambiguous ref, both act_on in scope: escalate AND block."""

    def test_act_on_ambiguity_escalates(self):
        C, D = A5AmbiguityWhateverTheGrade.C, A5AmbiguityWhateverTheGrade.D
        lead = fixed_lead(C, "x")
        lead["findings"] = [{"id": f"F{i}", "source_line": s, "verdict": "act_on", "category": "other",
                             "ordinary_use": "reception opens the client form",
                             "clinic_scenario": "reception on iPad opens the form",
                             "demonstrated": "reproduced label defect"} for i, s in enumerate((C, D))]
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", r1_seat(C), 1000),
                                     ("REVIEW_x_opus_r1_a.md", r1_seat(D), 1100),
                                     ("REVIEW_x_astra_r2_b.md", r2_file(prior=[("r1 P2 lib/label.ts:4", "closed")]), 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=2 ESCALATE=2", out)
        self.assertIn("FIXED=0", out)


class C5NewestRoundIsTheRoster(unittest.TestCase):
    """3bm73 (a) — r6's PASS seats at the tip wrote no priors; r7's seats at the same tip close every ref."""
    OTHER = "P2 lib/other.ts:5 — receipt total off by a penny — round it"

    def run3(self, r2_prior=(), r3_opus_prior=((REF, "closed"),), r2_findings=(), lead=None):
        r2 = lambda fs: closing_r2(strict_block("FAIL" if fs else "PASS", fs, prior=list(r2_prior))
                                   ).replace("\nPASS\n", "\nFAIL\n" if fs else "\nPASS\n")
        r3 = lambda p: closing_r2(strict_block("PASS", prior=list(p)))
        return run_named(lead or fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
            ("REVIEW_x_astra_r2_b.md", r2(r2_findings), 2000), ("REVIEW_x_opus_r2_b.md", r2(()), 2100),
            ("REVIEW_x_astra_r3_c.md", r3([(REF, "closed")]), 3000), ("REVIEW_x_opus_r3_c.md", r3(r3_opus_prior), 3100)])

    def test_earlier_same_tip_silence_does_not_veto(self):
        code, out = self.run3()
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_earlier_same_tip_open_entry_escalates(self):
        code, out = self.run3(r2_prior=[(REF, "open")])
        self.assertIn("ESCALATE=1", out)
        self.assertIn("FIXED=0", out)

    def test_newest_round_silence_still_vetoes(self):
        code, out = self.run3(r3_opus_prior=())
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=0", out)

    def test_earlier_same_tip_finding_not_closed_blocks(self):
        rf = [("P2", "lib/other.ts:5", "receipt total off by a penny — round it")]
        extra = {"id": "F2", "source_line": self.OTHER, "verdict": "act_on", "category": "money",
                 "ordinary_use": "reception takes a card payment", "clinic_scenario": "reception at the desk on the iPad",
                 "demonstrated": "red test on the receipt total"}
        code, out = self.run3(r2_findings=rf, lead=fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md", [extra]))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=1", out)
        # Graded fixed by r3 at the same commit r2 raised it: never fixed.
        extra2 = dict(extra, verdict="fixed", fixed_by="@D@/REVIEW_x_astra_r3_c.md")
        code, out = self.run3(r2_findings=rf, lead=fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md", [extra2]))
        self.assertNotIn("FIXED=2", out)
        self.assertEqual(code, 1, out)


class C6NonTipProseRef(unittest.TestCase):
    """3bm73 (b) — r3's line 8 cites 'r1 P3 ...use-consent-client.ts:39', which names no r1 finding's location."""
    NOTE = "- r1 P3 components/appointments/use-consent-client.ts:39 closed: Three tests protect the HTTP error throw\n"

    def run_notes(self, note_in_tip):
        r2 = closing_r2(strict_block("PASS", prior=[]), tail="" if note_in_tip else self.NOTE).replace(SHA_B, SHA_C)
        r3 = closing_r2(tail=self.NOTE if note_in_tip else "")
        return run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", r2, 2000),
            ("REVIEW_x_astra_r3_c.md", r3, 3000)])

    def test_non_tip_unmatched_note_is_printed_not_refused(self):
        code, out = self.run_notes(note_in_tip=False)
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)
        self.assertIn("NOTE REVIEW_x_astra_r2_b.md: note on r1 P3 components/appointments/use-consent-client.ts:39", out)

    def test_tip_unmatched_note_still_refuses(self):
        code, out = self.run_notes(note_in_tip=True)
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_astra_r3_c.md: note on r1 P3 components/appointments/use-consent-client.ts:39", out)
        self.assertNotIn("FIXED=", out)


class C7Real3bm73R7(unittest.TestCase):
    """The PROOF: untouched 3bm73 r1-r6 (r1 under _r1_ names) + two synthetic strict r7 seats at 4f1c34f25e
    closing all seven refs: FIXED=7, the line-less r1 P3 escalates, r3's line-8 ref is a NOTE."""
    DIR = base.HERE / "fixtures" / "e3opf-c-3bm73-r7"

    def test_seven_fixed(self):
        named = [(p.name, p.read_text(), 1000 + i) for i, p in enumerate(sorted(self.DIR.glob("REVIEW_*.md")))]
        lead = json.loads((self.DIR / "lead.json").read_text())
        code, out = run_named(lead, named)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=1 FOLLOWUPS=0 DISMISSED=0 NOTED=0 P4=0 CLEARED=0 FIXED=7", out)
        self.assertIn("NOTE REVIEW_3bm73_astra-xhigh_r3_2026-10-24.md", out)


class C3SameRoundNeverConfirms(unittest.TestCase):
    """C3 below main(): fixed_errors alone never lets a review confirm one of its own round, whatever the mtimes."""

    def test_same_round_fixed_by_refused_in_check(self):
        raise_f = ("/x/REVIEW_x_astra_r1_a.md", [R1_SRC], R1_RAISE)
        conf = ("/x/REVIEW_x_opus_r1_b.md", [], closing_r2())
        lead = fixed_lead(R1_SRC, "/x/REVIEW_x_opus_r1_b.md")
        errs, _ = chk.check(lead, [R1_SRC], 2, False, [raise_f, conf])
        self.assertTrue(any("is not newer than" in e for e in errs), errs)


class OwnGateR1Attempt5(unittest.TestCase):
    """Own-gate r1 (Astra + Opus :1186, Opus :374): builder evidence never picks the newest round or tip; the
    two naming schemes are never mixed."""

    def test_higher_round_mutants_file_does_not_empty_the_roster(self):
        mutants = "PASS\n- KILLED retry guard reverted — pay suite red\n"  # echo-free (D1)
        base_files = [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                      ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2000),
                      ("REVIEW_x_opus_r2_b.md", closing_r2(strict_block("PASS", prior=[])), 2100)]
        for extra in ([], [("mutants-round-3.txt", mutants, 3000)]):
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), base_files + extra)
            self.assertIn("BLOCKING=1", out, extra)
            self.assertIn("FIXED=0", out, extra)

    def test_mixed_naming_schemes_refused(self):
        reraise = r1_seat(R1_SRC).replace(SHA_A, SHA_C)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000),
            ("opus-h2h-round-1.md", reraise, 3000)])
        self.assertEqual(code, 1, out)
        self.assertIn("mix _r<N>_", out)
        self.assertNotIn("FIXED=", out)

    def test_builder_evidence_on_another_commit_never_sets_the_tip(self):
        mut = lambda sha, n: (n, "PASS\n- KILLED retry guard reverted — pay suite red\n", 3000)  # echo-free (D1)
        seats = [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                 ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2000),
                 ("REVIEW_x_opus_r2_b.md", closing_r2(), 2100)]
        for extra in ([mut(SHA_C, "mutants-round-3.txt")],
                      [mut(SHA_C, "mutants-round-3.txt"), mut(SHA_A, "qa-output-round-3.log")]):
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), seats + extra)
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKING=0 ESCALATE=0", out)
            self.assertIn("FIXED=1", out)

    def test_builder_evidence_in_a_higher_round_does_not_hide_a_two_commit_round(self):
        mutants = ("mutants-round-3.txt", "PASS\n- KILLED guard — red\n", 3000)  # echo-free (D1)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000),
            ("REVIEW_x_opus_r2_b.md", closing_r2(strict_block("PASS", prior=[(REF, "open")])).replace(SHA_B, SHA_C), 2100),
            mutants])
        self.assertEqual(code, 1, out)
        self.assertIn("the newest round 2 echoes different commits", out)

    def test_native_rounds_from_two_attempt_dirs_refused(self):
        """Own-gate r2 Astra :398 / Opus :391: attempt-1's round-2 closure vs attempt-2's round-1 re-raise."""
        import os, tempfile
        d = base.Path(tempfile.mkdtemp(dir="/private/tmp"))
        (d / "a1").mkdir(); (d / "a2").mkdir()
        reraise = closing_r2(strict_block("FAIL", [("P2", "lib/pay.ts:37", "retry charges twice — guard")],
                                          prior=[(REF, "open")])).replace(SHA_B, SHA_C).replace("\nPASS\n", "\nFAIL\n")
        files = {d / "a1/astra-verdict-round-1.log": R1_RAISE, d / "a1/astra-verdict-round-2.log": closing_r2(),
                 d / "a2/opus-h2h-round-1.md": reraise}
        for p, t in files.items():
            p.write_text(t)
        (d / "lead.json").write_text(json.dumps(fixed_lead(R1_SRC, str(d / "a1/astra-verdict-round-2.log"))))
        argv = ["--lead", str(d / "lead.json"), "--out-dir", str(d / "out"), "--round", "2"]
        for p in files:
            argv += ["--verdict", str(p)]
        err, out = base.io.StringIO(), base.io.StringIO()
        with base.redirect_stderr(err), base.redirect_stdout(out):
            code = chk.main(argv)
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("round-<N> files from 2 directories", err.getvalue())
        self.assertNotIn("FIXED=", out.getvalue())


# ---------------------------------------------------------------------------------------------
# LUCY-e3opf attempt 6: the two naming P2s of the independent Astra review of 4435274
# (Ops/reviews/REVIEW_e3opf_structured_astra-high_r5_2026-10-24.md). Names never decide identity.
BLOCKLESS_TIP = f"REVIEWING: test @ {SHA_B}\nPASS\n"  # Astra's :173 repro: echo + PASS, no block


class D1ReviewContentNeverHidesBehindAName(unittest.TestCase):
    """:173 — a blockless review renamed like builder evidence left the roster and the tip: FIXED=1."""

    def test_blockless_seat_renamed_to_builder_evidence_refused(self):
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")
        closer = ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)
        # Control: under a review name it is a seat with no prior entry, so the finding stays blocking.
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                                     ("REVIEW_x_opus_r2_b.md", BLOCKLESS_TIP, 2000), closer])
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=0", out)
        for name in ("mutants-round-2.txt", "qa-output-round-2.log"):
            code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), (name, BLOCKLESS_TIP, 2000),
                                         closer])
            self.assertEqual(code, 1, out)
            self.assertIn(f"{name}: review content in a builder-evidence file", out)
            self.assertNotIn("FIXED=", out)

    def test_newer_blockless_review_renamed_cannot_leave_tip_selection(self):
        """Without D1 the r3 review on SHA_C dropped out and r2's closure at SHA_B was the tip."""
        newer = BLOCKLESS_TIP.replace(SHA_B, SHA_C)
        files = [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                 ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2000)]
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              files + [("REVIEW_x_opus_r3_c.md", newer, 3000)])  # control: r3 is the tip
        self.assertNotIn("FIXED=1", out)
        for name in ("mutants-round-3.txt", "qa-tsc-round-3.log"):
            code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), files + [(name, newer, 3000)])
            self.assertEqual(code, 1, out)
            self.assertIn(f"{name}: review content in a builder-evidence file", out)
            self.assertNotIn("FIXED=", out)

    def test_any_echo_or_opener_line_is_review_content(self):
        for text in (BLOCKLESS_TIP, "PASS\nREVIEWING: half-written\n", "  REVIEWING: x @ abc\nREVIEWING: x @ abc\n",
                     "PASS\n```review-verdict\n", "PASS\n- ```review-verdict\n{}\n```\n", r2_file(prior=[(REF, "closed")])):
            with self.subTest(text=text[:40]):
                try:
                    hit = chk.has_review_content("mutants-round-2.txt", text)
                except ValueError:
                    hit = True  # a malformed block refuses the run anyway
                self.assertTrue(hit)
        self.assertFalse(chk.has_review_content("mutants-round-2.txt", "PASS\n- KILLED guard — red\n"))
        self.assertFalse(chk.has_review_content("qa-output-round-2.log", "$ npm test\nreviewing: lower case is text\n"))

    def test_echo_free_builder_evidence_needs_no_echo_and_other_inputs_still_do(self):
        mutants = ("mutants-round-2.txt", "PASS\n- KILLED retry guard reverted — pay suite red\n", 2050)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), mutants,
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_opus_r2_b.md", "PASS\nVERIFIED OK\n- tests\n", 2050),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "closed")]), 2100)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_opus_r2_b.md: no single well-formed REVIEWING echo", out)


class D2OneRoundTokenPerName(unittest.TestCase):
    """:391 — opus_r1_h2h-round-1.md matched both schemes; only _r1_ was seen, hiding the native namespace."""

    def reraise(self, tail=""):
        return closing_r2(strict_block("FAIL", [("P2", "lib/pay.ts:37", "retry charges twice — guard")],
                                       prior=[(REF, "open")]), tail=tail).replace(SHA_B, SHA_C).replace(
            "\nPASS\n", "\nFAIL\n")

    def run_abc(self, c_name, c_text):
        return run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), ("REVIEW_x_astra_r2_b.md", closing_r2(), 2000),
            (c_name, c_text, 3000)])

    def test_dual_scheme_name_refused(self):
        code, out = self.run_abc("opus-h2h-round-1.md", self.reraise())  # control: mixed schemes, refused
        self.assertEqual(code, 1, out)
        for name in ("opus_r1_h2h-round-1.md", "REVIEW_x_r1_opus_r1_c.md", "astra-round-1-verdict-round-1.log"):
            code, out = self.run_abc(name, self.reraise())
            self.assertEqual(code, 1, out)
            self.assertIn(f"{name}: the name states a round 2 times", out)
            self.assertNotIn("FIXED=", out)

    def test_c6_bypass_through_a_dual_scheme_name_refused(self):
        note = "- r1 P3 components/appointments/use-consent-client.ts:39 closed: tests cover it\n"
        code, out = self.run_abc("opus_r1_h2h-round-1.md", self.reraise(tail=note))
        self.assertEqual(code, 1, out)
        self.assertIn("opus_r1_h2h-round-1.md: the name states a round 2 times", out)
        self.assertNotIn("FIXED=", out)
        self.assertNotIn("NOTE ", out)

    def test_lone_dual_scheme_input_refused_cleanly(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/x"), [("REVIEW_x_r1_round-1.md", R1_RAISE, 1000)])
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_r1_round-1.md: the name states a round 2 times", out)
        self.assertNotIn("Traceback", out)

    def test_round_of_counts_every_match(self):
        self.assertEqual(chk.round_of("opus-h2h-round-3.md"), 3)
        self.assertEqual(chk.round_of("REVIEW_x_astra-xhigh_r12_2026-10-24.md"), 12)
        for name in ("opus_r1_h2h-round-1.md", "REVIEW_x_r2_y_r2_.md", "a-round-2-b-round-2.md"):
            with self.assertRaisesRegex(ValueError, "one round token per file"):
                chk.round_of(name)


class D1OwnGateR1BuilderEvidenceByRound(unittest.TestCase):
    """Own-gate r1 Opus :1209 / :1189: echo-free builder evidence is placed by its round, never by an echo."""
    LABEL = "P2 lib/label.ts:4 — the label shows the wrong treatment name"
    SURVIVOR = "P2 lib/label.ts:4 — mutant survived: label fix reverted, suite still green"
    REF = "r1 P2 lib/label.ts:4"

    def lead(self, by):
        return fixed_lead(self.LABEL, by, extra=[{
            "id": "F2", "source_line": self.SURVIVOR, "verdict": "dismissed", "category": "other",
            "reason": "the closing seat re-checked the label at this tip"}])

    def files(self, r1, r2, mutants):
        raise1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{self.LABEL}\nVERIFIED OK\n- tests\n"
        close2 = r2_file(prior=[(self.REF, "closed")])
        return [(r1, raise1, 1000), (r2, close2, 2100), (mutants, f"FAIL\n{self.SURVIVOR}\n", 2000)]

    def test_newest_round_survivor_still_vetoes(self):
        for r1, r2, mut in (("opus-h2h-round-1.md", "opus-h2h-round-2.md", "mutants-round-2.txt"),
                            ("opus-h2h-round-1.md", "opus-h2h-round-2.md", "mutants-round-3.txt"),
                            ("REVIEW_x_astra_r1_a.md", "REVIEW_x_astra_r2_b.md", "mutants-round-1.txt")):  # schemes differ
            code, out = run_named(self.lead(f"@D@/{r2}"), self.files(r1, r2, mut))
            self.assertEqual(code, 1, (mut, out))
            self.assertIn(f"{mut} raises P2 at lib/label.ts:4 again at the current tip", out)
            self.assertNotIn("FIXED=", out)

    def test_earlier_round_survivor_can_be_graded_fixed(self):
        survivor = "P2 lib/pay.ts:37 — mutant survived: retry guard reverted — add a retry test"
        files = [("opus-h2h-round-1.md", f"REVIEWING: goal/x @ {SHA_A}\nPASS\nVERIFIED OK\n- tests\n", 1000),
                 ("mutants-round-1.txt", f"FAIL\n{survivor}\n", 1100),
                 ("opus-h2h-round-2.md", r2_file(prior=[(REF, "closed")]), 2000)]
        code, out = run_named(fixed_lead(survivor, "@D@/opus-h2h-round-2.md"), files)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)
        # The same survivor in the newest round raises it at the tip: refused, never fixed.
        files[1] = ("mutants-round-2.txt", f"FAIL\n{survivor}\n", 1100)
        code, out = run_named(fixed_lead(survivor, "@D@/opus-h2h-round-2.md"), files)
        self.assertEqual(code, 1, out)
        self.assertIn("mutants-round-2.txt (builder evidence) raises it at the current tip", out)
        self.assertNotIn("FIXED=", out)

    def test_whitespace_prefixed_echo_is_review_content(self):
        for ch in ("\x0c", " ", "\t"):
            self.assertTrue(chk.has_review_content("mutants-round-2.txt", f"{ch}REVIEWING: test @ {SHA_B}\nPASS\n"), repr(ch))


class D1OwnGateR2BuilderEvidenceTakesItsRoundsCommit(unittest.TestCase):
    """Own-gate r2 Opus :1172: an earlier-round survivor raised at the commit the newest round reviewed (a
    no-new-commit re-review) is at the tip, as a seat raising it there is; one from a seatless round too."""
    SURV = "P2 lib/pay.ts:37 — mutant survived: retry guard reverted — add a retry test"

    def run_with(self, r1_sha, seatless=False):
        files = [("mutants-round-1.txt", f"FAIL\n{self.SURV}\n", 1100),
                 ("opus-h2h-round-2.md", r2_file(prior=[(REF, "closed")]), 2000)]
        if not seatless:
            files.insert(0, ("opus-h2h-round-1.md", f"REVIEWING: goal/x @ {r1_sha}\nPASS\nVERIFIED OK\n- tests\n", 1000))
        return run_named(fixed_lead(self.SURV, "@D@/opus-h2h-round-2.md"), files)

    def test_same_commit_earlier_round_survivor_refused(self):
        code, out = self.run_with(SHA_B)
        self.assertEqual(code, 1, out)
        self.assertIn("mutants-round-1.txt (builder evidence) raises it at the current tip", out)
        self.assertNotIn("FIXED=", out)

    def test_seatless_round_survivor_refused(self):
        code, out = self.run_with(SHA_A, seatless=True)
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=", out)

    def test_other_commit_earlier_round_survivor_fixed(self):
        code, out = self.run_with(SHA_A)
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)


# LUCY-cwpxu: a finding raised with no line is closed by a path-only ref 'r<N> <sev> <path>', and only then.
LL_SRC = "P3 tests/a.test.ts — the failed-fetch guard the contraindication checker relies on has no test — add one"
LL_REF = "r1 P3 tests/a.test.ts"


def lineless_r1(*srcs):
    """A round-1 raising review in the legacy {severity, text} shape, each finding with no line."""
    return (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n" + "".join(s + "\n" for s in srcs) + "```review-verdict\n"
            + json.dumps({"verdict": "FAIL", "findings": [{"severity": s[:2], "text": s[3:]} for s in srcs]})
            + "\n```\n")


def leads_for(srcs, by="@D@/REVIEW_x_astra_r2_b.md"):
    lead = fixed_lead(srcs[0], by)
    lead["findings"] = [dict(lead["findings"][0], id=f"F{i + 1}", source_line=s) for i, s in enumerate(srcs)]
    return lead


class CwpxuPathOnlyRef(unittest.TestCase):
    def test_every_newest_seat_closes_a_line_less_finding_fixed(self):
        """The 3bm73 shape: line-less, protected wording, every r2 seat closes it by path → FIXED, no escalation."""
        code, out = run_named(fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_opus_r2_b.md", r2_file(prior=[(LL_REF, "closed")]), 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed")]), 2100)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_one_silent_seat_keeps_it_not_fixed(self):
        """Every-seat-prior (B1) is unchanged for a path-only ref."""
        code, out = run_named(fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_opus_r2_b.md", r2_file(prior=[]), 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed")]), 2100)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1", out)
        self.assertIn("FIXED=0", out)

    def test_open_entry_disputes_a_path_only_close(self):
        code, out = run_named(fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_opus_r2_b.md", r2_file(prior=[(LL_REF, "open")]), 2000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed")]), 2100)])
        self.assertIn("ESCALATE=1", out)
        self.assertIn("FIXED=0", out)

    def test_path_only_ref_to_a_finding_with_a_line_refused(self):
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                                     ("REVIEW_x_astra_r2_b.md", r2_file(prior=[("r1 P2 lib/pay.ts", "closed")]), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("prior ref r1 P2 lib/pay.ts (closed) matches no round-1 finding", out)
        self.assertNotIn("FIXED=", out)

    def test_path_only_ref_matching_nothing_refused(self):
        code, out = run_named(fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed"), ("r1 P3 tests/b.test.ts", "closed")]), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("prior ref r1 P3 tests/b.test.ts (closed) matches no round-1 finding", out)

    def test_wrong_severity_path_only_ref_refused(self):
        code, out = run_named(fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md"), [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[("r1 P2 tests/a.test.ts", "closed")]), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("matches no round-1 finding", out)

    def test_two_line_less_findings_at_one_path_escalate(self):
        other = "P3 tests/a.test.ts — the empty-record cache has no test — add one"
        for named in ([("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC, other), 1000)],
                      [("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
                       ("REVIEW_x_astra_r1_a.md", lineless_r1(other), 1100)]):
            code, out = run_named(leads_for([LL_SRC, other]),
                                  named + [("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed")]), 2000)])
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKING=0 ESCALATE=2", out)
            self.assertIn("FIXED=0", out)

    def test_line_less_and_lined_at_one_path_escalate(self):
        """'Exactly one round-N finding at that path + severity': a lined sibling makes the path-only ref ambiguous.
        Since LUCY-unykv the lined sibling, closed at its own file:line by every newest seat, is fixed; the path-only
        finding still escalates (every_seat_closes never speaks for a path-only ref)."""
        lined = "P3 tests/a.test.ts:12 — the 500 cell asserts nothing — assert the blank tokens"
        raise_r1 = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{LL_SRC}\n{lined}\n" + strict_block(
            "FAIL", [("P3", "tests/a.test.ts:12", lined[3:])]).replace(
            '"findings": [', '"findings": [' + json.dumps({"severity": "P3", "text": LL_SRC[3:]}) + ", "))
        code, out = run_named(leads_for([LL_SRC, lined]), [
            ("REVIEW_x_sonnet_r1_a.md", raise_r1, 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed"), ("r1 P3 tests/a.test.ts:12", "closed")]), 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=1", out)
        self.assertIn("FIXED=1", out)
        files = [("REVIEW_x_sonnet_r1_a.md", [LL_SRC, lined], raise_r1),
                 ("REVIEW_x_astra_r2_b.md", [], r2_file(prior=[(LL_REF, "closed")]))]
        self.assertFalse(chk.every_seat_closes(LL_SRC, files))  # the escalated one is the path-only finding

    def test_raised_again_at_the_path_at_the_tip_not_fixed(self):
        again = "P3 tests/a.test.ts:40 — still no failed-fetch cell — add one"
        lead = fixed_lead(LL_SRC, "@D@/REVIEW_x_astra_r2_b.md",
                          extra=[{"id": "F2", "source_line": again, "verdict": "noted", "category": "other",
                                  "reason": "test-only coverage gap, not user-facing"}])
        code, out = run_named(lead, [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(LL_SRC), 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(LL_REF, "closed")], verdict="FAIL",
                                               findings=[("P3", again[3:])]), 2000)])
        # r2 redesign: a path-only closure can't tell a re-raise from a new defect, so it escalates.
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=2", out)
        self.assertIn("FIXED=0", out)


class CwpxuReal3bm73R7(unittest.TestCase):
    """The 3bm73 r1-r7 replay (C7's fixture) with both r7 seats also closing the line-less r1 P3 by path, and the
    lead grading it fixed: nothing left to escalate."""
    DIR = C7Real3bm73R7.DIR
    PATH_REF = "r1 P3 tests/components/appointments/workflow-consent-medical-history.test.tsx"

    def named(self, refs):
        out = []
        for i, p in enumerate(sorted(self.DIR.glob("REVIEW_*.md"))):
            text = p.read_text()
            if "_r7_" in p.name:
                entries = "".join(f', {{"ref": "{r}", "status": "closed", "evidence": "three failed-read cells; the '
                                  f'failed_fetch mutant turns them red"}}' for r in refs)
                text = text.replace('"}]}\n```', '"}' + entries + ']}\n```')
                self.assertIn(refs[0] if refs else "", text)
            out.append((p.name, text, 1000 + i))
        return out

    def lead(self):
        lead = json.loads((self.DIR / "lead.json").read_text())
        f8 = next(f for f in lead["findings"] if f["id"] == "F8")
        for k in ("reason", "follow_up"):
            f8.pop(k)
        f8.update(verdict="fixed", category="other", fixed_by="@D@/REVIEW_3bm73_astra-xhigh_r7_2026-10-24.md")
        return lead

    def test_path_only_prior_clears_the_last_escalation(self):
        code, out = run_named(self.lead(), self.named([self.PATH_REF]))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0 FOLLOWUPS=0 DISMISSED=0 NOTED=0 P4=0 CLEARED=0 FIXED=8", out)

    def test_without_the_path_only_prior_it_is_refused(self):
        code, out = run_named(self.lead(), self.named([]))
        self.assertEqual(code, 1, out)
        self.assertIn(f'F8: fixed_by REVIEW_3bm73_astra-xhigh_r7_2026-10-24.md has no review-verdict prior entry '
                      f'{{"ref": "{self.PATH_REF}", "status": "closed"}}', out)


class CwpxuExactToken(unittest.TestCase):
    """Attempt 3 (Manager DECOUPLE, 25 Oct): a path-only ref is exact. Eligible finding = '<SEV> <TOKEN> — ...' with
    TOKEN in [A-Za-z0-9._@()[]/-]+ and no ./ a/ b/ / prefix or '..'; ref TOKEN byte-equal; any other finding in any
    round naming TOKEN or its file name (case-insensitive, whitespace deleted) escalates. Every repro from the
    independent r1 and r2 reviews refuses or escalates."""
    BY = "@D@/REVIEW_x_astra_r2_b.md"
    REAL = ("P3 tests/components/appointments/workflow-consent-medical-history.test.tsx — the contraindication "
            "checker's failed-read path has no test — add one")

    def close(self, src, ref, tip=None, raise_r1=None, lead=None):
        return run_named(lead or fixed_lead(src, self.BY), [
            ("REVIEW_x_sonnet_r1_a.md", raise_r1 or lineless_r1(src), 1000),
            ("REVIEW_x_astra_r2_b.md", tip or r2_file(prior=[(f"r1 P3 {ref}", "closed")]), 2000)])

    def assert_closed(self, res):
        code, out = res
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def assert_refused(self, res, ref=None):
        code, out = res
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=", out)
        if ref:
            self.assertIn(f"r1 P3 {ref}", out)

    def assert_escalated(self, res, n=2):
        code, out = res
        self.assertEqual(code, 0, out)
        self.assertIn(f"ESCALATE={n}", out)
        self.assertIn("FIXED=0", out)

    def test_path_only_token(self):
        for line, tok in [(LL_SRC, "tests/a.test.ts"), (self.REAL, self.REAL.split()[1]),
                          ("P3 Dockerfile — x", "Dockerfile"), ("P3 main.c - x", "main.c"),
                          ("P3 voice-service/.dockerignore — x", "voice-service/.dockerignore"),
                          ("P3 app/(booking)/[id]/page.tsx — x", "app/(booking)/[id]/page.tsx")]:
            self.assertEqual(chk.path_only_token(line), tok, line)
            self.assertEqual(chk.ref_loc(line), tok, line)
        for line in ["P3 `src/a.ts` — x", "P3 **src/a.ts** — x", "P3 _src/a.ts_ — x", "P3 ./src/a.ts — x",
                     "P3 a/src/a.ts — x", "P3 b/src/a.ts — x", "P3 /src/a.ts — x", "P3 src/../a.ts — x",
                     "P3 src/a.ts#L12 — x", "P3 [src/a.ts](src/a.ts) — x", "P3 https://x.io/src/a.ts — x",
                     "P3 src/my test.ts — x", "P3 `src/my test.ts` — x", "P3 src/a.ts – x", "P3 src/a.ts —x",
                     "P3 src/ — x", "P3 src//a.ts — x", "P3 P1/P2 — x", "P3 src/$id.tsx — x", "P3 R&D.md — x",
                     "P3 @ — x", "P3 () — x", "P3 [] — x", "P3 - — x"]:
            self.assertIsNone(chk.path_only_token(line), line)
        self.assertEqual(chk.ref_loc("P2 lib/a.ts:12 — x"), "lib/a.ts:12")

    def test_honest_tokens_close(self):
        """r1 :677: extensionless, short, dotfile and route names close by their exact token."""
        for path in ("tests/a.test.ts", "Dockerfile", "main.c", "voice-service/Makefile", "voice-service/.dockerignore",
                     "app/(booking)/[id]/page.tsx", "lib/@scope/x_y-z.ts"):
            src = f"P3 {path} — the build skips the contraindication checker's tests — run them"
            with self.subTest(path=path):
                self.assert_closed(self.close(src, path))

    def test_hyphen_separator_closes(self):
        src = "P3 tests/a.test.ts - the contraindication checker's failed-fetch guard has no test"
        self.assert_closed(self.close(src, "tests/a.test.ts"))

    def test_ref_is_byte_equal(self):
        for token, ref in [("SRC/a.ts", "src/a.ts"), ("src/a.ts", "SRC/a.ts"), ("src/a.ts", "src/a.ts.")]:
            src = f"P3 {token} — the contraindication checker lacks a regression assertion — add it"
            with self.subTest(token=token, ref=ref):
                self.assert_refused(self.close(src, ref), ref)

    def test_ineligible_findings_refuse(self):
        """Independent r1 :545, r2 :575, :546, :616: a wrapped, lined, prefixed, linked or spaced location is not
        eligible, so the path-only ref matches nothing and refuses the run."""
        for token, ref in [("`src/a.ts`:12", "src/a.ts"), ("**src/a.ts**:12", "src/a.ts"), ("**src/a.ts:12**", "src/a.ts"),
                           ("`src/a.ts`", "src/a.ts"), ("./src/a.ts", "src/a.ts"),
                           ("a/src/a.ts", "src/a.ts"), ("b/src/a.ts", "src/a.ts"),
                           ("[src/a.ts](https://github.com/org/repo/blob/main/src/a.ts#L12)", "src/a.ts"),
                           ("[src/a.ts](src/a.ts#L12)", "src/a.ts"), ("[src/a.ts](src/a.ts)", "src/a.ts"),
                           ("src/a.ts#L12", "src/a.ts"), ("`src/my test.ts`", "src/my"), ("src/my test.ts", "src/my")]:
            src = f"P3 {token} — the contraindication checker lacks a regression assertion — add it"
            with self.subTest(token=token):
                self.assert_refused(self.close(src, ref), ref)

    def test_same_round_other_spelling_escalates(self):
        """Independent r1 :557: src/a.ts and ./src/a.ts in one round. Close one + note the other: both escalate."""
        a = "P3 src/a.ts — the contraindication checker lacks the first assertion — add it"
        for token in ("./src/a.ts", "src/a.ts", "a/src/a.ts", "`src/a.ts`", "src//a.ts", "SRC/A.TS", "src/a.ts#L12",
                      "[x](https://github.com/org/repo/blob/main/src/a.ts#L12)", "lib/other.ts:3 (see a.ts)"):
            b = f"P3 {token} — a second missing assertion — add it"
            lead = fixed_lead(a, self.BY, [{"id": "F2", "source_line": b, "verdict": "noted", "category": "other",
                                            "reason": "test-only coverage gap, not user-facing"}])
            with self.subTest(token=token):
                self.assert_escalated(self.close(a, "src/a.ts", raise_r1=lineless_r1(a, b), lead=lead))

    def tip_mention(self, again, src=LL_SRC, ref="tests/a.test.ts"):
        lead = fixed_lead(src, self.BY, [{"id": "F2", "source_line": again, "verdict": "noted", "category": "other",
                                          "reason": "test-only coverage gap, not user-facing"}])
        return self.close(src, ref, lead=lead, tip=r2_file(prior=[(f"r1 P3 {ref}", "closed")], verdict="FAIL",
                                                            findings=[("P3", again[3:])]))

    def test_tip_mention_escalates(self):
        """Independent r1 :1308 and r2 :631: a tip finding naming the path anyhow (bare, link, URL, anchor, split
        across a line, zero-width space, other case, the file name alone) escalates the closure."""
        for mention in ("tests/a.test.ts", "./tests/a.test.ts", "`tests/a.test.ts`", "tests/a.test.ts#L40",
                        "https://github.com/org/repo/blob/main/tests/a.test.ts#L12",
                        "[the test](https://github.com/org/repo/blob/main/tests/a.test.ts#L12)",
                        "[the test](tests/a.test.ts#L40)", "tests/\na.test.ts", "tests/ a.test.ts",
                        "tests/\u200ba.test.ts", "TESTS/A.TEST.TS", "a.test.ts", "tests/a.test.ts:12"):
            again = f"P3 lib/b.ts:5 — {mention} still lacks the failed-fetch assertion — add it"
            with self.subTest(mention=mention):
                self.assert_escalated(self.tip_mention(again))

    def test_split_file_name_mention_escalates(self):
        """Whitespace and zero-width characters inside the file name itself are deleted before matching."""
        for mention in ("tests/a.\ntest.ts", "tests/a. test.ts", "tests/a.\u200btest.ts", "tests/a.\u2060test.ts"):
            again = f"P3 lib/b.ts:5 — {mention} still lacks the failed-fetch assertion — add it"
            with self.subTest(mention=mention):
                self.assert_escalated(self.tip_mention(again))

    def test_trimmed_and_unbracketed_needles_escalate(self):
        """A token with route marks or edge punctuation is also matched without them (more escalations only)."""
        for src_tok, mention in (("app/[id].tsx", "app/id.tsx"), ("lib/x/a.ts.", "lib/x/a.ts")):
            src = f"P3 {src_tok} — the contraindication read has no failed-fetch cell — add one"
            again = f"P3 lib/b.ts:5 — {mention} still lacks it — add it"
            with self.subTest(src_tok=src_tok):
                self.assert_escalated(self.tip_mention(again, src, src_tok))

    def test_route_path_mention_escalates(self):
        src = "P3 pages/api/clients/[id]/medical-history.ts — the contraindication read has no failed-fetch cell"
        for again in ("P3 lib/b.ts:5 — [the handler](pages/api/clients/[id]/medical-history.ts) still lacks it",
                      "P3 lib/b.ts:5 — pages/api/clients/id/medical-history.ts still lacks it"):
            with self.subTest(again=again):
                self.assert_escalated(self.tip_mention(again, src, "pages/api/clients/[id]/medical-history.ts"))

    def test_file_name_noise_escalates(self):
        """Fail closed: a.test.tsx contains a.test.ts. Noise escalations are accepted."""
        self.assert_escalated(self.tip_mention("P3 lib/b.ts:5 — tests/a.test.tsx lacks the empty-state cell — add it"))

    def test_unrelated_tip_finding_still_closes(self):
        code, out = self.tip_mention("P3 lib/b.ts:5 — the empty state renders nothing — add a message")
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_closing_prose_is_not_a_finding(self):
        """CLARIFY (C): only findings are the haystack. The closer's own prose naming the path doesn't escalate."""
        self.assert_closed(self.close(LL_SRC, "tests/a.test.ts", tip=r2_file(
            prior=[(LL_REF, "closed")], confirm="- tests/a.test.ts now has three failed-fetch cells; mutant red")))

    def test_other_round_any_severity_escalates(self):
        later = "P1 lib/c.ts:30 — tests/a.test.ts mocks the guard away — use the real one"
        files = [("REVIEW_x_r1_a.md", [LL_SRC], ""), ("REVIEW_x_r2_a.md", [later], "")]
        self.assertEqual(chk.ref_ambiguity(files, 1, "P3", "tests/a.test.ts"), sorted([LL_SRC, later]))
        self.assertEqual(chk.ref_ambiguity(files[:1], 1, "P3", "tests/a.test.ts"), [LL_SRC])
        self.assertEqual(chk.ref_ambiguity(files, 1, "P2", "tests/a.test.ts"), [])

    def test_same_text_in_two_seats_is_one_finding(self):
        files = [("REVIEW_x_sonnet_r1_a.md", [LL_SRC], ""), ("REVIEW_x_astra_r1_a.md", [LL_SRC], "")]
        self.assertEqual(chk.ref_ambiguity(files, 1, "P3", "tests/a.test.ts"), [LL_SRC])


class CwpxuAttempt4(CwpxuExactToken):
    """Attempt 4 (Manager re-plan after independent r3, 25 Oct). (E) a legacy finding's text runs on through its
    continuation lines, both for the ambiguity haystack and for the eligible finding's own hidden line. (F) the
    token's last segment is a plain file name, and a file with a markdown reference definition has no path-only
    findings. (G) NFKC, and every Unicode format character deleted, before matching."""
    A = "P3 src/a.ts — the contraindication checker lacks a regression assertion — add it"
    FIRST = "P3 tests/regression.test.ts:20 — the missing assertion"

    def legacy_r1(self, *bullets, tail=""):
        return f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n" + "".join(f"- {b}\n" for b in bullets) + tail

    def noted(self, line):
        return [{"id": "F2", "source_line": line, "verdict": "noted", "category": "other",
                 "reason": "test-only assertion gap with no runtime effect"}]

    def test_continuation_mention_escalates(self):
        """Independent r3 :1227: a mention wrapped onto a continuation line is part of that finding."""
        for cont in ("\n  src/a.ts still needs adding", " in src/a.\n  ts still needs adding",
                     ".\n  See `src/a.ts`; the failure case is untested.", "\nsrc/a.ts, unindented, still counts"):
            raising = self.legacy_r1(self.A, self.FIRST + cont)
            first = (self.FIRST + cont).split("\n")[0]
            with self.subTest(cont=cont):
                self.assert_escalated(self.close(self.A, "src/a.ts", raise_r1=raising,
                                                 lead=fixed_lead(self.A, self.BY, self.noted(first))))

    def test_continuation_stops_at_a_boundary(self):
        """A blank line, the next bullet, a heading, a fence or a section ends the finding: prose past it is not
        a finding, so the honest closure still goes through."""
        for tail in ("\nsrc/a.ts is mentioned in a later paragraph\n", "- src/a.ts in a non-finding bullet\n",
                     "## Notes on src/a.ts\n", "```\nsrc/a.ts\n```\n", "VERIFIED OK\nsrc/a.ts checked\n"):
            raising = self.legacy_r1(self.A, self.FIRST, tail=tail)
            with self.subTest(tail=tail):
                self.assert_closed(self.close(self.A, "src/a.ts", raise_r1=raising,
                                              lead=fixed_lead(self.A, self.BY, self.noted(self.FIRST))))

    def test_current_tip_wrapped_reraise_escalates(self):
        """Independent r3 :1227: a blockless review of the current tip re-raising on a continuation line."""
        later = f"REVIEWING: goal/x @ {SHA_B}\nFAIL\n- {self.FIRST} in\n  src/a.ts still needs adding\n"
        lead = fixed_lead(self.A, "@D@/REVIEW_x_astra_r3_c.md", self.noted(self.FIRST + " in"))
        self.assert_escalated(run_named(lead, [
            ("REVIEW_x_sonnet_r1_a.md", lineless_r1(self.A), 1000),
            ("REVIEW_x_opus_r2_b.md", later, 2000),
            ("REVIEW_x_astra_r3_c.md", r2_file(prior=[("r1 P3 src/a.ts", "closed")]), 3000)]))

    def test_own_continuation_line_refuses(self):
        """The eligible finding's own text giving the token a line (':N', '#L', '(N)') makes it lined after all."""
        for cont in ("see src/a.ts:12", "see src/a.ts#L12", "see src/a.ts(12)", "see src/a.ts :12",
                     "see SRC/A.TS:12"):
            with self.subTest(cont=cont):
                self.assert_refused(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A + "\n  " + cont)))
        self.assert_refused(self.close(self.A + " — at src/a.ts:12", "src/a.ts"))

    def test_own_text_without_a_line_closes(self):
        for cont in ("see src/a.ts for the checker", "src/a.ts: the checker", "src/a.tsx:12 is a sibling"):
            with self.subTest(cont=cont):
                self.assert_closed(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A + "\n  " + cont)))

    def test_reference_link_tokens_are_not_tokens(self):
        """Independent r3 :556: reference-style links and a trailing (N) are not plain tokens."""
        for line in ("P3 [src/a.ts][source] — x", "P3 src/a.ts(12) — x", "P3 src/a.ts[12] — x", "P3 (src/a.ts) — x",
                     "P3 [src/a.ts] — x", "P3 [src/a.ts][12] — x", "P3 src/[a.ts] — x", "P3 src/a.ts/(x) — x"):
            self.assertIsNone(chk.path_only_token(line), line)
        for line, tok in (("P3 app/(booking)/page.tsx — x", "app/(booking)/page.tsx"),
                          ("P3 app/[id]/page.tsx — x", "app/[id]/page.tsx"),
                          ("P3 pages/clients/[id].tsx — x", "pages/clients/[id].tsx")):
            self.assertEqual(chk.path_only_token(line), tok, line)

    def test_reference_link_repros_refuse(self):
        for tok, definition in (("[src/a.ts][source]", "[source]: src/a.ts#L12"),
                                ("[src/a.ts][source]", "[source]: src/other.ts#L12"),
                                ("src/a.ts(12)", "")):
            a = f"P3 {tok} — the contraindication checker lacks a regression assertion — add it"
            with self.subTest(tok=tok):
                self.assert_refused(self.close(a, tok, raise_r1=self.legacy_r1(a, tail=f"\n{definition}\n")))

    def test_reference_definition_bars_the_file(self):
        """Any reference definition in the raising file bars its plain tokens too (fail closed)."""
        self.assert_refused(self.close(self.A, "src/a.ts",
                                       raise_r1=self.legacy_r1(self.A, tail="\n[source]: src/a.ts#L12\n")))
        self.assert_refused(self.close(self.A, "src/a.ts", raise_r1=lineless_r1(self.A) + "\n  [x]: lib/b.ts\n"))
        self.assert_closed(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A, tail="\nsee [x] later\n")))

    def test_route_file_names_close(self):
        for path in ("pages/clients/[id].tsx", "app/(booking)/page.tsx", "app/[id]/page.tsx"):
            src = f"P3 {path} — the contraindication checker's failed-read path has no test — add one"
            with self.subTest(path=path):
                self.assert_closed(self.close(src, path))

    def test_format_and_compatibility_characters_escalate(self):
        """(G): U+2063 and a soft hyphen are format characters; NFKC folds full-width letters and the division
        slash. Cross-script homoglyphs (Cyrillic а) stay a documented crafted-only residual."""
        for mention in ("src/a.⁣ts", "src/a.­ts", "ｓrc/a.ts", "src∕a.ts", "src/a․ts"):
            again = f"P3 lib/b.ts:5 — {mention} still lacks the regression assertion — add it"
            with self.subTest(mention=mention):
                self.assert_escalated(self.tip_mention(again, self.A, "src/a.ts"))
        code, out = self.tip_mention("P3 lib/b.ts:5 — src/а.ts still lacks it — add it", self.A, "src/a.ts")
        self.assertIn("FIXED=1", out)  # the residual, pinned so a fix shows up here


class CwpxuAttempt4OwnGateR1(CwpxuAttempt4):
    """Attempt 4 own-gate r1 (Astra xhigh): a line behind closing marks or a query, a '#123' continuation that
    is not a heading, and reference definitions without a space or inside a container."""

    def test_line_behind_closing_marks_refuses(self):
        for cont in ("See `src/a.ts`:12", "see **src/a.ts**#L3", "src/a.ts?plain=1#L12", "src/a.ts, line 12",
                     "src/a.ts (line 12)", "src/a.ts — L12", "src/a.ts#123"):
            with self.subTest(cont=cont):
                self.assert_refused(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A + "\n  " + cont)))

    def test_numbered_description_still_closes(self):
        a = "P3 src/a.ts — (1) the contraindication checker lacks a regression assertion — add it"
        self.assert_closed(self.close(a, "src/a.ts"))

    def test_issue_number_continuation_is_not_a_heading(self):
        for cont in ("\n#123 in src/a.ts still lacks it", "\n#src/a.ts still lacks it", "\n  #123 src/a.ts"):
            first = self.FIRST
            raising = self.legacy_r1(self.A, first + cont)
            with self.subTest(cont=cont):
                self.assert_escalated(self.close(self.A, "src/a.ts", raise_r1=raising,
                                                 lead=fixed_lead(self.A, self.BY, self.noted(first))))

    def test_every_reference_definition_form_bars(self):
        route = "P3 pages/clients/[id].tsx — the contraindication checker lacks a regression assertion — add it"
        for definition in ("[id]:src/a.ts#L12", "[id]: src/a.ts#L12", "   [id]:<src/a.ts#L12>", "> [id]: src/a.ts#L12",
                           "- [id]: src/a.ts#L12", "1. [id]: src/a.ts#L12", "[i\nd]: src/a.ts#L12"):
            with self.subTest(definition=definition):
                self.assert_refused(self.close(route, "pages/clients/[id].tsx",
                                               raise_r1=self.legacy_r1(route, tail=f"\n{definition}\n")))
        self.assert_closed(self.close(route, "pages/clients/[id].tsx",
                                      raise_r1=self.legacy_r1(route, tail="\nthe [id] segment is the client id\n")))


class CwpxuAttempt4OwnGateR2(CwpxuAttempt4):
    """Attempt 4 own-gate r2 (Astra xhigh): an inline ```span``` is not a fence, and one first line continued
    differently in two seats is two findings a single path-only ref can't close."""

    def test_inline_triple_backtick_span_keeps_the_line(self):
        for cont in ("```src/a.ts```:12", "```src/a.ts``` (line 12)"):
            with self.subTest(cont=cont):
                self.assert_refused(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A + "\n" + cont)))
        # A real fence still ends the finding: its body is not the finding's text.
        self.assert_closed(self.close(self.A, "src/a.ts", raise_r1=self.legacy_r1(self.A, tail="```ts\nsrc/a.ts:12\n```\n")))

    def seats(self, cont_a, cont_b):
        return run_named(leads_for([self.A, self.A]), [
            ("REVIEW_x_sonnet_r1_a.md", self.legacy_r1(self.A + cont_a), 1000),
            ("REVIEW_x_opus_r1_a.md", self.legacy_r1(self.A + cont_b), 1100),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[("r1 P3 src/a.ts", "closed")]), 2000)])

    def test_differing_continuations_escalate(self):
        for a, b in (("\n  on a timeout", "\n  on an empty response"), ("\n  on a timeout", "")):
            with self.subTest(a=a, b=b):
                self.assert_escalated(self.seats(a, b), n=2)

    def test_identical_continuations_close(self):
        for cont in ("", "\n  on a timeout"):
            with self.subTest(cont=cont):
                code, out = self.seats(cont, cont)
                self.assertEqual(code, 0, out)
                self.assertIn("BLOCKING=0 ESCALATE=0", out)
                self.assertIn("FIXED=2", out)
