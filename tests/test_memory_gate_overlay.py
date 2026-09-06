"""Exercise overlay installation, repeatability and all-or-nothing conflicts."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-memory-gate-gstack.py'


class OverlayTest(unittest.TestCase):
    def test_install_repeat_check_and_conflict(self):
        patches = sorted((ROOT/'patches/memory-gate').glob('*.patch'))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(target)], check=True)
            # Reconstruct minimal files from each patch's original context. This
            # makes the tests portable without needing a live gstack checkout.
            for patch in patches:
                lines = patch.read_text().splitlines(True)
                relative = lines[0].strip().removeprefix('--- a/')
                before = ''.join(line[1:] for line in lines[3:] if line.startswith((' ', '-')))
                file = target / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(before)
            def run(*args):
                return subprocess.run([sys.executable, str(INSTALLER), str(target), *args],
                                      text=True, capture_output=True)
            originals = {p: p.read_text() for p in target.rglob('*') if p.is_file() and '.git' not in p.parts}
            self.assertEqual(run('--check').returncode, 0)
            self.assertTrue(all(p.read_text() == content for p, content in originals.items()))
            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            installed = {p: p.read_text() for p in originals}
            self.assertTrue(all('Exit 75 means NOT RUN' in content for content in installed.values()))
            self.assertEqual(run().returncode, 0)
            self.assertTrue(all(p.read_text() == content for p, content in installed.items()))
            for p, content in originals.items():
                p.write_text(content)
            broken = next(iter(originals))
            broken.write_text('upstream changed this section\n')
            self.assertNotEqual(run().returncode, 0)
            self.assertTrue(all(p == broken or p.read_text() == content for p, content in originals.items()))


if __name__ == '__main__':
    unittest.main()
