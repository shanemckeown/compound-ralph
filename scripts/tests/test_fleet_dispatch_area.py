"""Check 14 of fleet-dispatch.py: a single bead needs exactly one area:<slug> label (vault-reorg Phase 3).
Run: python3 -m unittest discover -s ~/.claude/scripts/tests"""
import importlib.util
import os
import unittest

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)


def rec(labels):
    return {"id": "AestheticcNext-test1", "labels": labels}


class AreaLabelCheck(unittest.TestCase):
    def test_one_valid_area_passes(self):
        self.assertEqual(fd.check_area_label(rec(["headless-eligible", "area:product"])), [])

    def test_no_area_refused(self):
        problems = fd.check_area_label(rec(["headless-eligible"]))
        self.assertEqual(len(problems), 1)
        self.assertIn("no area: label", problems[0])
        self.assertIn("bd label add AestheticcNext-test1 area:", problems[0])

    def test_unknown_area_refused(self):
        problems = fd.check_area_label(rec(["area:growth"]))
        self.assertIn("unknown area label", problems[0])

    def test_two_areas_refused(self):
        problems = fd.check_area_label(rec(["area:sales", "area:marketing"]))
        self.assertIn("keep exactly one", problems[0])

    def test_missing_labels_key_refused(self):
        self.assertEqual(len(fd.check_area_label({"id": "AestheticcNext-test1"})), 1)


if __name__ == "__main__":
    unittest.main()
