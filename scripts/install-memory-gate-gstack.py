#!/usr/bin/env python3
"""Apply the versioned gstack overlay. Explicit target; --check never writes.

Run after integrating the compound-ralph branch:
  python3 scripts/install-memory-gate-gstack.py ~/.claude/skills/gstack --check
  python3 scripts/install-memory-gate-gstack.py ~/.claude/skills/gstack
Preserves unrelated gstack edits. Context conflicts stop before any file changes.
"""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gstack', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = args.gstack.expanduser().resolve()
    patches = sorted((Path(__file__).resolve().parents[1] / 'patches/memory-gate').glob('*.patch'))
    pending = []
    for patch in patches:
        command = ['git', 'apply', '--check', str(patch)]
        check = subprocess.run(command, cwd=target, capture_output=True, text=True)
        if check.returncode == 0:
            pending.append(str(patch))
        elif subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                            cwd=target, capture_output=True).returncode == 0:
            print('Already installed:', patch.name)
        else:
            parser.exit(1, 'Overlay conflicts; no files changed: ' + patch.name + '\n' + check.stderr)
    if pending:
        # git apply checks every hunk before writing; no partial --reject mode.
        subprocess.run(['git', 'apply', *(['--check'] if args.check else []), *pending],
                       cwd=target, check=True)
    print(('Checked' if args.check else 'Installed') + f' memory-gate overlay ({len(pending)} pending patches).')


if __name__ == '__main__':
    main()
