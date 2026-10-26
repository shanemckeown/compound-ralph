"""Tests for review-effort.py (LUCY-nd3qz): the Astra effort is computed and fails closed to xhigh.

Run: python3 -m unittest discover -s ~/.claude/scripts/tests -p 'test_review_effort.py' -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_claude_only_guard import FAKE_BD, REAL_ALLOWLIST, SCRIPTS

SCRIPT = SCRIPTS / "review-effort.py"
BEADS = {
    "AestheticcNext-ui1": {"labels": ["area:ui", "client:viso"], "parent": None},
    "AestheticcNext-pd1": {"labels": ["patient-data"], "parent": None},
    "AestheticcNext-pay1": {"labels": ["area:payments"], "parent": None},
    "AestheticcNext-kid": {"labels": ["area:ui"], "parent": "AestheticcNext-pay1"},
    "AestheticcNext-down": "ERROR",
}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


class ReviewEffort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "db.json").write_text(json.dumps(BEADS))
        bd = t / "bd"
        bd.write_text(FAKE_BD)
        bd.chmod(0o755)
        self.env = dict(os.environ, FAKE_BD_DB=str(t / "db.json"), CLAUDE_ONLY_BD=str(bd),
                        CLAUDE_ONLY_ALLOWLIST=str(REAL_ALLOWLIST))
        self.repo = t / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "README.md").write_text("x\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "base")
        self.base = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()

    def tearDown(self):
        self.tmp.cleanup()

    def tip_with(self, *paths):
        git(self.repo, "checkout", "-q", "--detach", self.base)  # each tip's diff holds only its own paths
        for p in paths:
            f = self.repo / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("change\n")
            git(self.repo, "add", p)
        git(self.repo, "commit", "-qm", "tip")
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()

    def effort(self, bead, tip):
        r = subprocess.run([sys.executable, str(SCRIPT), bead, tip, "--repo", str(self.repo), "--base", self.base],
                           capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip()
        self.assertEqual(len(line.splitlines()), 1, line)
        return line.split()[0], line

    def test_ordinary_ui_only_diff_is_high(self):
        tip = self.tip_with("components/ui/Button.tsx", "app/globals.css")
        word, line = self.effort("AestheticcNext-ui1", tip)
        self.assertEqual(word, "high", line)

    def test_sensitive_path_is_xhigh(self):
        for p in ("lib/stripe/refund.ts", "drizzle/migrations/0412_x.sql", "lib/auth/session.ts",
                  "components/clients/Notes.tsx", "lib/gdpr/erase-client.ts"):
            with self.subTest(p):
                tip = self.tip_with(p)
                word, line = self.effort("AestheticcNext-ui1", tip)
                self.assertEqual(word, "xhigh", line)
                self.assertIn(p, line)

    def test_patient_data_label_is_xhigh(self):
        tip = self.tip_with("components/ui/Button.tsx")
        word, line = self.effort("AestheticcNext-pd1", tip)
        self.assertEqual(word, "xhigh", line)
        self.assertIn("patient-data", line)

    def test_money_label_and_parent_label_are_xhigh(self):
        tip = self.tip_with("components/ui/Button.tsx")
        for bead in ("AestheticcNext-pay1", "AestheticcNext-kid"):
            with self.subTest(bead):
                word, line = self.effort(bead, tip)
                self.assertEqual(word, "xhigh", line)

    def test_unknown_bead_bd_down_bad_tip_and_empty_diff_are_xhigh(self):
        tip = self.tip_with("components/ui/Button.tsx")
        for bead, t in (("AestheticcNext-nosuch", tip), ("AestheticcNext-down", tip),
                        ("AestheticcNext-ui1", "deadbeef" * 5), ("AestheticcNext-ui1", self.base)):
            with self.subTest(bead=bead, tip=t):
                word, line = self.effort(bead, t)
                self.assertEqual(word, "xhigh", line)
                self.assertIn("fail closed", line)

    def test_risk_tier_import_failure_is_xhigh(self):
        tip = self.tip_with("components/ui/Button.tsx")
        spec = importlib.util.spec_from_file_location("review_effort", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        saved = sys.modules.get("risk_tier", "absent")
        sys.modules["risk_tier"] = None  # import risk_tier now raises ImportError
        try:
            effort, reason = mod.decide("AestheticcNext-ui1", tip, str(self.repo), self.base)
        finally:
            if saved == "absent":
                sys.modules.pop("risk_tier", None)
            else:
                sys.modules["risk_tier"] = saved
        self.assertEqual(effort, "xhigh", reason)
        self.assertIn("risk_tier", reason)
        self.assertIn("fail closed", reason)

    def test_lists_are_imported_from_risk_tier_not_copied(self):
        src = SCRIPT.read_text()
        self.assertIn('import_module("risk_tier")', src)
        self.assertNotRegex(src, r"(?m)^(SENSITIVE|PATIENT_DOMAINS) = ")


if __name__ == "__main__":
    unittest.main()
