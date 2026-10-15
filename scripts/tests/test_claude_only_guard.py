"""Tests for claude_only_guard.py, bin/codex and the fleet wrappers (LUCY-b3hf5).

A Claude-only client's bead must never reach codex/Astra/GLM; every other bead routes as before.

Run: python3 -m unittest discover -s ~/.claude/scripts/tests -p 'test_claude_only_guard.py' -v
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
GUARD = SCRIPTS / "claude_only_guard.py"
SHIM = ROOT / "bin" / "codex"
FLEET_GUARDED = SCRIPTS / "fleet-guarded.sh"
CODEX_DISPATCH = SCRIPTS / "fleet-dispatch-codex.py"
REAL_ALLOWLIST = SCRIPTS / "claude-only-clients.json"

# Fake bead DB: id -> {"labels": [...], "parent": ...}, or "ERROR" to simulate bd being down.
BEADS = {
    "AestheticcNext-anita1": {"labels": ["client:dr-anita-dhunna", "area:voice"], "parent": None},
    "AestheticcNext-anita1.5": {"labels": ["area:voice"], "parent": None},  # dotted-id path only
    "AestheticcNext-kid": {"labels": [], "parent": "AestheticcNext-anita1"},
    "AestheticcNext-plain": {"labels": ["client:viso"], "parent": None},
    "AestheticcNext-i6xyx": {"labels": ["client:chris-flanagan"], "parent": None},
    "AestheticcNext-down": "ERROR",
}

FAKE_BD = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    db = json.load(open(os.environ["FAKE_BD_DB"]))
    if not sys.stdin.isatty():
        sys.stdin.read()  # an inherited pipe would eat codex's review brief
    bead = sys.argv[2]
    row = db.get(bead)
    if row is None:
        print('Error fetching %s: no issue found matching "%s"' % (bead, bead), file=sys.stderr)
        sys.exit(1)
    if row == "ERROR":
        print("dolt server unreachable", file=sys.stderr)
        sys.exit(1)
    print(json.dumps([dict(id=bead, labels=row["labels"], parent=row["parent"])]))
""")

FAKE_CODEX = "#!/bin/bash\necho \"REAL CODEX $*\"\n[ \"${!#}\" = - ] && echo \"STDIN:$(cat)\"\nexit 0\n"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "db.json").write_text(json.dumps(BEADS))
        bd = t / "bd"
        bd.write_text(FAKE_BD)
        bd.chmod(0o755)
        real = t / "real"
        real.mkdir()
        (real / "codex").write_text(FAKE_CODEX)
        (real / "codex").chmod(0o755)
        self.plain_dir = t / "plain"
        self.plain_dir.mkdir()
        self.env = {
            "PATH": f"{SHIM.parent}:{real}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "FAKE_BD_DB": str(t / "db.json"),
            "CLAUDE_ONLY_BD": str(bd),
            "CLAUDE_ONLY_ALLOWLIST": str(REAL_ALLOWLIST),
        }
        self.t = t

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, argv, cwd=None, stdin="", **env):  # never inherit the runner's stdin
        e = dict(self.env, **env)
        r = subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                           cwd=cwd or self.plain_dir, env=e, timeout=60, input=stdin)
        return r.returncode, r.stdout, r.stderr

    def route(self, bead):
        code, out, err = self.run_cmd([sys.executable, GUARD, "route", bead])
        self.assertEqual(code, 0, err)
        return out.split("\t")[0]

    # --- routing (the dry run the bead asks for) ---
    def test_labelled_bead_routes_claude(self):
        self.assertEqual(self.route("AestheticcNext-anita1"), "claude")

    def test_unlabelled_bead_routes_astra(self):
        self.assertEqual(self.route("AestheticcNext-plain"), "astra")

    def test_dotted_child_inherits(self):
        self.assertEqual(self.route("AestheticcNext-anita1.5"), "claude")

    def test_parent_field_inherits(self):
        self.assertEqual(self.route("AestheticcNext-kid"), "claude")

    def test_listed_bead_id_routes_claude(self):
        self.assertEqual(self.route("AestheticcNext-i6xyx"), "claude")

    def test_lookup_failure_routes_claude(self):
        self.assertEqual(self.route("AestheticcNext-down"), "claude")

    def test_shipped_allowlist(self):
        data = json.loads(REAL_ALLOWLIST.read_text())
        self.assertIn("client:dr-anita-dhunna", data["labels"])
        self.assertIn("AestheticcNext-i6xyx", data["beads"])

    # --- bin/codex shim ---
    def test_shim_refuses_labelled_bead(self):
        code, out, err = self.run_cmd([SHIM, "exec", "--sandbox", "read-only", "-"],
                                      BEAD_ID="AestheticcNext-anita1.5")
        self.assertNotEqual(code, 0)
        self.assertNotIn("REAL CODEX", out)
        self.assertIn("Claude only", err)

    def test_shim_passes_unlabelled_bead(self):
        code, out, err = self.run_cmd([SHIM, "exec", "-"], BEAD_ID="AestheticcNext-plain")
        self.assertEqual(code, 0, err)
        self.assertIn("REAL CODEX exec -", out)

    def test_shim_passes_stdin_through_untouched(self):
        brief = "review brief line 1\nline 2"
        code, out, err = self.run_cmd([SHIM, "exec", "-"], stdin=brief, BEAD_ID="AestheticcNext-plain")
        self.assertEqual(code, 0, err)
        self.assertIn("STDIN:" + brief, out)

    def test_shim_passes_without_bead_context(self):
        code, out, err = self.run_cmd([SHIM, "exec", "hello"])
        self.assertEqual(code, 0, err)
        self.assertIn("REAL CODEX", out)

    def test_shim_fails_closed_when_bd_down(self):
        code, out, err = self.run_cmd([SHIM, "exec", "-"], BEAD_ID="AestheticcNext-down")
        self.assertNotEqual(code, 0)
        self.assertNotIn("REAL CODEX", out)
        self.assertIn("cannot confirm", err)

    def test_shim_reads_branch_of_cd_dir(self):
        repo = self.t / "wt"
        repo.mkdir()
        for argv in (["git", "init", "-q", "-b", "goal/aestheticcnext-anita1", str(repo)],):
            subprocess.run(argv, check=True, capture_output=True)
        code, out, err = self.run_cmd([SHIM, "exec", "--cd", repo, "-"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("REAL CODEX", out)
        # and from the cwd with no --cd
        code, out, _ = self.run_cmd([SHIM, "exec", "-"], cwd=repo)
        self.assertNotEqual(code, 0)
        self.assertNotIn("REAL CODEX", out)

    def test_two_shim_copies_on_path_do_not_loop(self):
        copy = self.t / "copy"
        copy.mkdir()
        (copy / "codex").write_text(SHIM.read_text())
        (copy / "codex").chmod(0o755)
        path = f"{copy}:{self.env['PATH']}"
        code, out, err = self.run_cmd([SHIM, "exec", "-"], stdin="x", PATH=path, BEAD_ID="AestheticcNext-plain")
        self.assertEqual(code, 0, err)
        self.assertIn("REAL CODEX", out)

    def test_shim_version_is_not_checked(self):
        code, out, _ = self.run_cmd([SHIM, "--version"], BEAD_ID="AestheticcNext-anita1")
        self.assertEqual(code, 0)
        self.assertIn("REAL CODEX --version", out)

    # --- wrappers ---
    def test_fleet_guarded_refuses_codex_and_glm(self):
        for kind in ("codex", "glm"):
            code, out, err = self.run_cmd(
                ["bash", FLEET_GUARDED, kind, "AestheticcNext-anita1 phase4 review round-1",
                 "echo", "SENT"])
            self.assertNotEqual(code, 0, kind)
            self.assertNotIn("SENT", out, kind)
            self.assertIn("REFUSED", err, kind)

    def test_codex_dispatcher_refuses(self):
        code, out, err = self.run_cmd(
            [sys.executable, CODEX_DISPATCH, "dispatch", "AestheticcNext-anita1", "--dry-run", "--force"])
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("REFUSED", out)
        self.assertIn("Claude only", out)


if __name__ == "__main__":
    unittest.main()
