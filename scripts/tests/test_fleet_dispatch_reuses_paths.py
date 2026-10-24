"""LUCY-2tjdu: check 5's Reuses parser must keep paths with (), [] and spaces whole.
Run: python3 -m unittest discover -s ~/.claude/scripts/tests -p 'test_fleet_dispatch_reuses_paths.py'"""
import importlib.util
import os
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

PRIVACY = "app/(marketing)/privacy-policy/page.tsx"
VAULT_SPACED = "Aestheticc/Clients/Iconic Cosmetic Clinic/INDEX.md"


def exists_in(*paths):
    def check(p):
        return fd.reuse_path(p) in paths
    return check


class ReusesPathsWithParensAndSpaces(unittest.TestCase):
    def test_route_group_path_kept_whole(self):
        text = (f"Reuses: {PRIVACY}:1 (the existing public privacy policy page; edit its AI-location "
                "lines in place, no new page)")
        self.assertEqual(fd.check_reuses(text, "b", exists_in(PRIVACY)), ([], []))

    def test_dynamic_segment_path_kept_whole(self):
        p = "pages/api/clients/[id].ts"
        self.assertEqual(fd.check_reuses(f"Reuses: {p}:40", "b", exists_in(p)), ([], []))

    def test_bare_vault_path_with_spaces(self):
        text = f"Reuses: {VAULT_SPACED}:3 (the client index note)"
        self.assertEqual(fd.check_reuses(text, "b", exists_in(VAULT_SPACED)), ([], []))

    def test_quoted_and_backticked_paths_with_spaces(self):
        for text in (f'Reuses: "{VAULT_SPACED}" section 2', f"Reuses: `{VAULT_SPACED}:3`"):
            self.assertEqual(fd.check_reuses(text, "b", exists_in(VAULT_SPACED)), ([], []), text)

    def test_real_repo_route_group_path(self):
        # The real file in AestheticcNext (AestheticcNext-9iy0i's Reuses line).
        text = f"Reuses: {PRIVACY}:1 (the existing public privacy policy page)"
        self.assertEqual(fd.check_reuses(text, "AestheticcNext-9iy0i"), ([], []))

    def test_missing_paths_still_refused(self):
        for text, shown in ((f"Reuses: app/(marketing)/no-such-page/page.tsx:1 (gone)",
                             "app/(marketing)/no-such-page/page.tsx"),
                            ("Reuses: Aestheticc/Clients/No Such Clinic/INDEX.md", "INDEX.md"),
                            ('Reuses: "Aestheticc/Clients/No Such Clinic/INDEX.md"',
                             "Aestheticc/Clients/No Such Clinic/INDEX.md")):
            errs, _ = fd.check_reuses(text, "b", exists_in(PRIVACY, VAULT_SPACED))
            self.assertEqual(len(errs), 1, text)
            self.assertIn(shown, errs[0], text)

    def test_a_truncated_tail_does_not_pass_for_the_whole(self):
        # Only the tail exists: the path as written is still missing.
        errs, _ = fd.check_reuses(f"Reuses: {PRIVACY}:1", "b", exists_in("privacy-policy/page.tsx"))
        self.assertEqual(len(errs), 1)
        self.assertIn(PRIVACY, errs[0])


if __name__ == "__main__":
    unittest.main()
