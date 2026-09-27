"""harness-metrics.py (LUCY-o7otj): five weekly numbers from fixture bead exports, lead-round
JSON and Jev decisions logs. Run: python3 -m unittest discover -s ~/.claude/scripts/tests"""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest

SCRIPT = os.path.expanduser("~/.claude/scripts/harness-metrics.py")
spec = importlib.util.spec_from_file_location("harness_metrics", SCRIPT)
hm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hm)

UTC = datetime.timezone.utc
START = datetime.datetime(2026, 9, 21, tzinfo=UTC)
END = datetime.datetime(2026, 9, 28, tzinfo=UTC)
IN, BEFORE = "2026-09-25T10:00:00Z", "2026-09-10T10:00:00Z"
S1, S2 = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"


def c(text, at=IN):
    return {"text": text, "created_at": at}


BEADS = [
    # (a) first independent review in window: PASS, landed
    {"id": "AestheticcNext-a1", "status": "closed", "closed_at": IN, "created_at": BEFORE,
     "comments": [c("OWN-GATE REVIEW (builder's gate, NOT the independent review): PASS", "2026-09-24T09:00:00Z"),
                  c("INDEPENDENT REVIEW 2026-09-25 (tip abc): PASS — 0 P1"),
                  c("Review: FAIL — later re-review", "2026-09-26T10:00:00Z")]},
    # (a) first FAIL, then PASS on re-review: counts as FAIL; one REFLECTION in window
    {"id": "AestheticcNext-a2", "status": "closed", "closed_at": IN, "created_at": BEFORE,
     "close_reason": "landed da5e6bf1c",
     "comments": [c("ASTRA XHIGH INDEPENDENT REVIEW (tip x) + lead sort: FAIL — 1 blocking"),
                  c("REFLECTION attempt 2: the review failed on F1", "2026-09-25T12:00:00Z"),
                  c("Review: PASS", "2026-09-26T10:00:00Z")]},
    # (a) not landed: still pushed, or closed without landing → counted as in review, not in rate
    {"id": "AestheticcNext-a4", "status": "pushed", "created_at": BEFORE,
     "comments": [c("ASTRA XHIGH REVIEW 2026-09-25 (tip 496cdd5bd): PASS — 0 P1")]},
    {"id": "AestheticcNext-a5", "status": "closed", "closed_at": IN, "created_at": BEFORE,
     "close_reason": "SUPERSEDED by a6",
     "comments": [c("OPUS HEAD-TO-HEAD REVIEW 2026-09-25 (tip x): FAIL — 1 P2")]},
    # (a) builder's own review is not an independent review
    {"id": "AestheticcNext-a6", "status": "closed", "closed_at": IN, "created_at": BEFORE,
     "comments": [c("BUILDER IN-SESSION REVIEW 2026-09-24 (NOT the independent review): PASS")]},
    # (a) first review BEFORE window: excluded
    {"id": "AestheticcNext-a3", "status": "closed", "closed_at": IN, "created_at": BEFORE,
     "comments": [c("Review: FAIL", BEFORE), c("Review: PASS")]},
    # (b) qa-followups: filed in window (open), filed before + closed in window, filed+closed before
    {"id": "AestheticcNext-b1", "status": "open", "created_at": IN, "labels": ["qa-followup"]},
    {"id": "AestheticcNext-b2", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "labels": ["qa-followup"], "close_reason": "SHIPPED in 0.24"},
    {"id": "AestheticcNext-b3", "status": "closed", "created_at": BEFORE, "closed_at": BEFORE,
     "labels": ["qa-followup"], "close_reason": "FALSE-ALARM: old"},
    # (e) closed without a build
    {"id": "AestheticcNext-e1", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "FALSE-ALARM: builder verified on prod (read-only) that rows were separate"},
    {"id": "AestheticcNext-e2", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "NO-CODE: by design (Shane 27 Sep). Reply points Leah to Settings."},
    {"id": "LUCY-e3", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "Closed: this is by-design behaviour"},
    {"id": "AestheticcNext-e4", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "SUPERSEDED by e5"},
    # r8gzp.46's shape: a shipped fix whose reason mentions by-design later; and a negation
    {"id": "AestheticcNext-e5", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "SUPERSEDED: fixed on main — sweep 2026-09-24. Premise now decided: by design"},
    {"id": "AestheticcNext-e6", "status": "closed", "created_at": BEFORE, "closed_at": IN,
     "close_reason": "SHIPPED 0.24.1 — not by design, a real regression"},
]

LEADS = {
    "run1/lead-round-1.json": {"bead": "AestheticcNext-a2", "findings": [
        {"reason": "Shane question: should reception see every client in an own-clients-only clinic?"},
        {"follow_up": "Shane question: should the review lead skip pg mutants on docs-only beads?"},
        {"reason": "Act on: real bug"}]},
    # same question repeated next round → counted once
    "run1/lead-round-2.json": {"bead": "AestheticcNext-a2", "findings": [
        {"reason": "Shane question: should reception see every client in an own-clients-only clinic?"}],
        "premise": {"note": "Shane question — does a walk-in count as an existing client?"}},
    "old/lead-round-1.json": {"bead": "AestheticcNext-zz", "findings": [
        {"reason": "Shane question: outside the window"}]},
}

DRAFT = [
    {"ts": "2026-09-25T10:00:00Z", "session": S1, "allow": False, "why": "flagged callOffer"},
    {"ts": "2026-09-25T10:01:00Z", "session": S1, "allow": True, "why": "identical retry after a flag"},
    {"ts": "2026-09-25T11:00:00Z", "session": S2, "allow": False, "why": "flagged blame"},
    {"ts": "2026-09-25T11:02:00Z", "session": S2, "allow": True, "why": "clean (jev asked)"},
    {"ts": "2026-09-25T12:00:00Z", "session": "s", "allow": False, "why": "fixture"},
    {"ts": "2026-09-10T12:00:00Z", "session": S1, "allow": False, "why": "outside window"},
]
STOP = [
    {"ts": "2026-09-25T10:00:00Z", "session": S1, "exit": 2, "why": "blocked"},
    {"ts": "2026-09-25T10:05:00Z", "session": S1, "exit": 0, "why": "allowed"},
    {"ts": "2026-09-25T11:00:00Z", "session": S2, "exit": 2, "why": "blocked"},
    {"ts": "2026-09-25T11:05:00Z", "session": S2, "exit": 0, "why": "gate: nothing to check"},
    {"ts": "2026-09-25T12:00:00Z", "session": "11111111-2222-3333-4444-555555555555", "exit": 2,
     "why": "blocked"},
]


class HarnessMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.beads = os.path.join(root, "issues.jsonl")
        with open(cls.beads, "w") as fh:
            fh.write("\n".join(json.dumps(b) for b in BEADS) + "\n{\"_type\": \"memory\"}\nnot json\n")
        in_ts = datetime.datetime(2026, 9, 25, tzinfo=UTC).timestamp()
        old_ts = datetime.datetime(2026, 9, 1, tzinfo=UTC).timestamp()
        for rel, data in LEADS.items():
            path = os.path.join(root, "evidence", rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(data, fh)
            stamp = old_ts if rel.startswith("old/") else in_ts
            os.utime(path, (stamp, stamp))
        cls.lead_glob = os.path.join(root, "evidence", "*", "lead-round-*.json")
        cls.jev = os.path.join(root, "hooks")
        for name, rows in (("jev-draft-check", DRAFT), ("jev-stop-verify", STOP)):
            os.makedirs(os.path.join(cls.jev, name))
            with open(os.path.join(cls.jev, name, "decisions.log"), "w") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        cls.m = hm.collect(START, END, [cls.beads], cls.lead_glob, cls.jev)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_first_independent_review_pass_rate(self):
        a = self.m["a"]
        self.assertEqual((a["reviewed"], a["passed"], a["in_review"]), (2, 1, 2))
        self.assertEqual(a["failed_beads"], ["AestheticcNext-a2"])
        self.assertEqual(a["reflections"], 1)

    def test_b_qa_followups_filed_vs_closed(self):
        self.assertEqual(self.m["b"], {"filed": 1, "closed": 1, "open_total": 1})

    def test_c_shane_questions_deduped_and_tagged(self):
        self.assertEqual(self.m["c"], {"total": 3, "product": 2, "harness": 1, "beads": 1})

    def test_d_jev_true_hits_exclude_overrides_and_fixtures(self):
        d = self.m["d"]
        self.assertEqual(d["draft"], {"checks": 4, "denies": 2, "overridden": 1, "true_hits": 1,
                                      "fixture_rows": 1})
        self.assertEqual(d["stop"], {"checks": 4, "blocks": 2, "true_hits": 1, "fixture_rows": 1})

    def test_d_missing_logs_reported_not_zeroed(self):
        d = hm.jev_hits(os.path.join(self.tmp.name, "nope"), START, END)
        self.assertEqual(d, {"draft": None, "stop": None})
        self.assertIn("draft-check: no log", hm.render(dict(self.m, d=d), START, END, "x"))

    def test_e_closed_without_build(self):
        e = self.m["e"]
        self.assertEqual(e["FALSE-ALARM"], ["AestheticcNext-e1"])
        self.assertEqual(sorted(e["NO-CODE"]), ["AestheticcNext-e2"])
        self.assertEqual(e["by-design"], ["LUCY-e3"])

    def test_report_has_all_five_rows_and_replaces_same_week(self):
        section = hm.render(self.m, START, END, "fixture")
        for row in ("| a |", "| b |", "| c |", "| d |", "| e |"):
            self.assertIn(row, section)
        self.assertIn("1/2 = **50%**", section)
        out = os.path.join(self.tmp.name, "HARNESS_METRICS.md")
        hm.write_report(out, section)
        earlier = hm.render(self.m, START - datetime.timedelta(days=7), START, "fixture")
        hm.write_report(out, earlier)
        hm.write_report(out, section)   # same week again → replaced, not duplicated
        text = open(out).read()
        self.assertTrue(text.startswith(hm.HEADER))
        self.assertEqual(text.count("## Week 2026-09-21"), 1)
        self.assertEqual(text.count("## Week 2026-09-14"), 1)
        self.assertLess(text.index("## Week 2026-09-21"), text.index("## Week 2026-09-14"))

    def test_report_never_prints_comment_text(self):
        section = hm.render(self.m, START, END, "fixture")
        self.assertNotIn("reception", section)
        self.assertNotIn("Leah", section)


if __name__ == "__main__":
    unittest.main()
