"""Tests for goal-plan-fields.py, the /goal Phase 2b plan-field check (LUCY-fuu2h).

Run: python3 -m unittest discover -s ~/.claude/scripts/tests -p 'test_goal_plan_fields.py' -v
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "goal-plan-fields.py"

FULL = """# Plan
**OUTCOME PROMISE:** pressing Save stores the expiry for the clinic.
**PREMISE CHECK:** SELECT count(*) ... -> 3 rows.
**EXISTING CAPABILITY:**
- EXTEND lib/account-credit.ts:40 — add an expiry column.
**TOUCHPOINT MAP:** lib/account-credit.ts:40, pages/api/credit.ts:12
**HAZARDS:** H-M5 n/a.
**SHANE QUESTIONS:** none
**ACCEPTANCE TESTS:** tests/credit.test.ts
"""


def run(text):
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        p = Path(d) / "PLAN.md"
        p.write_text(text)
        r = subprocess.run([sys.executable, str(SCRIPT), str(p)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


class PlanFields(unittest.TestCase):
    def test_full_plan_passes(self):
        code, out = run(FULL)
        self.assertEqual(code, 0, out)

    def test_plan_without_existing_capability_fails(self):
        text = "\n".join(l for l in FULL.splitlines() if "EXISTING CAPABILITY" not in l and "EXTEND" not in l)
        code, out = run(text)
        self.assertEqual(code, 1, out)
        self.assertIn("missing field: EXISTING CAPABILITY", out)

    def test_empty_existing_capability_fails(self):
        code, out = run(FULL.replace("- EXTEND lib/account-credit.ts:40 — add an expiry column.\n", ""))
        self.assertEqual(code, 1, out)
        self.assertIn("EXISTING CAPABILITY: list each existing path", out)

    def test_new_path_without_reason_fails(self):
        code, out = run(FULL.replace("- EXTEND lib/account-credit.ts:40 — add an expiry column.",
                                     "- NEW PATH lib/consult-credit/ledger.ts"))
        self.assertEqual(code, 1, out)
        self.assertIn("NEW PATH with no written reason", out)

    def test_new_path_with_reason_passes(self):
        code, out = run(FULL.replace(
            "- EXTEND lib/account-credit.ts:40 — add an expiry column.",
            "- NEW PATH lib/voice/stream.ts — no existing module streams audio; Twilio media is new here"))
        self.assertEqual(code, 0, out)

    def test_none_with_reason_passes_on_the_heading_line(self):
        text = FULL.replace("**EXISTING CAPABILITY:**\n- EXTEND lib/account-credit.ts:40 — add an expiry column.",
                            "3. **EXISTING CAPABILITY.** none — first integration with this vendor, nothing similar")
        code, out = run(text)
        self.assertEqual(code, 0, out)

    def test_bare_none_fails(self):
        code, out = run(FULL.replace("- EXTEND lib/account-credit.ts:40 — add an expiry column.", "none"))
        self.assertEqual(code, 1, out)

    def test_extend_without_path_fails(self):
        code, out = run(FULL.replace("- EXTEND lib/account-credit.ts:40 — add an expiry column.",
                                     "- EXTEND the credit thing"))
        self.assertEqual(code, 1, out)
        self.assertIn("names no file path", out)

    def _cap(self, line):
        return run(FULL.replace("- EXTEND lib/account-credit.ts:40 — add an expiry column.", line))

    def test_bare_filename_new_path_without_reason_fails(self):
        code, out = self._cap("- NEW PATH reuse-capability-check.py")
        self.assertEqual(code, 1, out)
        self.assertIn("NEW PATH with no written reason", out)

    def test_root_level_extend_passes(self):
        code, out = self._cap("- EXTEND Makefile:12 — add a target")
        self.assertEqual(code, 0, out)

    def test_short_real_reasons_pass(self):
        self.assertEqual(self._cap("- NEW PATH lib/new.ts — Vendor API differs.")[0], 0)
        self.assertEqual(self._cap("- none — New vendor.")[0], 0)

    def test_keywords_in_prose_are_not_declarations(self):
        code, out = self._cap("- none — No existing module to EXTEND; this is the first vendor integration.")
        self.assertEqual(code, 0, out)
        code, out = self._cap("- none — nothing to EXTEND, and no NEW PATH needed elsewhere")
        self.assertEqual(code, 0, out)

    def test_prose_line_mentioning_extend_is_not_a_declaration(self):
        code, out = self._cap("- EXTEND lib/a.ts:1 — add a column\n"
                              "  We weighed whether to EXTEND the old table and kept it.")
        self.assertEqual(code, 0, out)

    def test_backticked_path_with_space_needs_its_own_reason(self):
        code, out = self._cap("- NEW PATH `docs/Architecture Notes.md`")
        self.assertEqual(code, 1, out)
        self.assertIn("NEW PATH with no written reason", out)
        code, out = self._cap("- NEW PATH `docs/Architecture Notes.md` — no existing architecture doc")
        self.assertEqual(code, 0, out)

    def test_extensionless_and_dotfile_new_paths_pass_with_reason(self):
        for name in ("Dockerfile", "Makefile", ".gitignore"):
            code, out = self._cap(f"- NEW PATH {name} — none exists in this repo yet")
            self.assertEqual(code, 0, f"{name}: {out}")

    def test_field_out_of_order_fails(self):
        text = FULL.replace("**EXISTING CAPABILITY:**\n- EXTEND lib/account-credit.ts:40 — add an expiry column.\n", "")
        text = text.replace("**ACCEPTANCE TESTS:**",
                            "**EXISTING CAPABILITY:** EXTEND lib/a.ts:1\n**ACCEPTANCE TESTS:**")
        code, out = run(text)
        self.assertEqual(code, 1, out)
        self.assertIn("out of order", out)


if __name__ == "__main__":
    unittest.main()
