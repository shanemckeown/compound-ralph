#!/usr/bin/env python3
"""AestheticcNext-yoi4o: isolated Bash 3.2/modern Bash regression checks.

Run: python3 hooks/night-batch/test-morning-reports.py
Print both dry-run DMs: append --transcripts
All fixtures live in $TMPDIR (or the system temp directory). PATH stubs
replace bd/gh/git/curl/date; curl has no networking code and rejects any token
except the fixture token. --dry-run additionally rejects any curl invocation.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parent
BASHES = [Path('/bin/bash'), Path('/opt/homebrew/bin/bash')]
BASHES = [str(path) for path in BASHES if path.exists()]

# One local Python stub, symlinked as each external dependency. There is no
# subprocess, HTTP library, socket or exec fallback to the real executables.
STUB = r'''#!PYTHON
import datetime, json, os, pathlib, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['NIGHT_BATCH_ROOT'])
if name == 'date':
    day = datetime.date.fromisoformat(os.environ.get('FAKE_TODAY', '2026-09-09'))
    clock = os.environ.get('FAKE_CLOCK', '07:00')
    formats = {'+%Y-%m-%d': str(day), '+%FT%TZ': str(day) + 'T06:00:00Z',
               '+%Y-%m-%d %H:%M %Z': str(day) + ' ' + clock + ' BST',
               '+%a %d %b %Y %H:%M %Z': day.strftime('%a %d %b %Y') + ' ' + clock + ' BST'}
    print(formats[args[-1]])
elif name == 'bd':
    assert args[0] == 'show'
    title = {'AestheticcNext-ready': 'Fix "quoted" reminder',
             'AestheticcNext-review': 'Review consent checks',
             'AestheticcNext-guard': 'Investigate oversized diff'}.get(args[1], 'Fixture ' + args[1])
    print(args[1] + ' · ' + title)
elif name == 'gh':
    if os.environ.get('FAKE_LOOKUP_DELAY'):
        (root / 'lookup-started').touch()
        time.sleep(float(os.environ['FAKE_LOOKUP_DELAY']))
    if args[:2] == ['pr', 'list']:
        bead = args[args.index('--head') + 1].removeprefix('auto/')
        number = {'AestheticcNext-ready': '101', 'AestheticcNext-review': '102'}.get(bead, '')
        if os.environ.get('FAKE_NO_PRS'):
            number = ''
        if number:
            print(number if 'number' in args else 'https://example.invalid/pull/' + number)
    elif args[:2] == ['pr', 'diff']:
        print('.beads/issues.jsonl' if os.environ.get('FAKE_DIRTY_PR') else 'src/reminders.ts')
    else:
        raise AssertionError(args)
elif name == 'git':
    sys.exit(1)
elif name == 'curl':
    assert os.environ.get('FAKE_TRANSPORT') == '1', 'curl forbidden during dry-run'
    assert 'Authorization: Bearer xoxb-FAKE-NOT-A-TOKEN' in args, 'non-fixture token rejected'
    payload_arg = args[args.index('--data-binary') + 1]
    assert payload_arg.startswith('@')
    payload = json.loads(pathlib.Path(payload_arg[1:]).read_text())
    assert payload['channel'] == 'U_FAKE_SHANE'
    with (root / 'curl-calls.jsonl').open('a') as out:
        out.write(json.dumps(payload) + '\n')
    time.sleep(float(os.environ.get('FAKE_CURL_DELAY', '0')))
    mode = os.environ.get('FAKE_CURL_MODE', 'ok')
    if mode == 'transport':
        sys.exit(22)
    elif mode == 'api':
        print('{"ok": false, "error": "invalid_auth"}')
    elif mode == 'malformed':
        print('not JSON')
    else:
        print('{"ok": true, "ts": "123.456", "channel": "U_FAKE_SHANE"}')
else:
    raise AssertionError(name)
'''


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.test-morning-', dir=os.environ.get('TMPDIR') or None)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.bin = self.root / 'bin'
        for directory in [self.state, self.bin, self.root / 'logs', self.root / 'dreams', self.root / 'home']:
            directory.mkdir()
        stub = self.bin / 'stub'
        stub.write_text(STUB.replace('#!PYTHON', '#!' + sys.executable))
        stub.chmod(0o755)
        for name in ['date', 'bd', 'gh', 'git', 'curl']:
            (self.bin / name).symlink_to('stub')
        self.credentials = self.root / 'fake.env'
        self.credentials.write_text('SLACK_BOT_TOKEN="xoxb-FAKE-NOT-A-TOKEN"\n')
        # Construct a fresh environment; no inherited Slack, GitHub or beads token.
        self.env = {
            'PATH': str(self.bin) + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'HOME': str(self.root / 'home'), 'TZ': 'Europe/London',
            'LANG': 'en_GB.UTF-8', 'NIGHT_BATCH_ROOT': str(self.root),
            'NIGHT_BATCH_TODO': str(self.root / 'TODO.md'),
            'NIGHT_BATCH_DREAM_ROOT': str(self.root / 'dreams'),
            'NIGHT_BATCH_WORKTREES': str(self.root / 'worktrees'),
            'NIGHT_BATCH_SLACK_ENV': str(self.credentials),
            'BEADS_DIR': str(self.root / 'fake-beads'),
            'SHANE_SLACK_USER_ID': 'U_FAKE_SHANE', 'FAKE_TODAY': '2026-09-09',
        }

    def close(self):
        self.temp.cleanup()

    def marker(self, bead, status='guarded', day=None, content='1788900000\n'):
        directory = self.state / 'processed' / day if day else self.state
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (bead + '.' + status)
        path.write_text(content)
        return path

    def rich(self):
        ready = self.marker('AestheticcNext-ready', 'complete', '2026-09-08')
        self.marker('AestheticcNext-review', 'reviewed', '2026-09-09')
        self.marker('AestheticcNext-guard')
        ready.with_suffix('.preview-url').write_text('status=ready\nurl=https://example.invalid/preview\n')
        for bead in ['ready', 'review']:
            (self.root / 'logs' / f'AestheticcNext-{bead}.log').write_text('bd-cleanup: force-pushed\n')
        codex = self.state / 'gate3/AestheticcNext-review/codex.log'
        codex.parent.mkdir(parents=True)
        codex.write_text('[BLOCKER] Missing consent check\n')
        (self.root / 'logs/orchestrator.log').write_text(
            '[2026-09-08T21:00:00Z] === night-batch start\n'
            '[2026-09-08T21:30:00Z] poll: dispatched=3/15\n')

    def run(self, script='morning-report', args=('--dry-run',), bash='/bin/bash', **env):
        return subprocess.run([bash, str(SCRIPTS / (script + '.sh')), *args],
                              env=self.env | env, capture_output=True, text=True, timeout=20)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def calls(self):
        path = self.root / 'curl-calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def outbox(self, script='morning-report', day='2026-09-09'):
        return self.state / (script + '-delivery') / day


class Reports(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def test_dry_runs_are_read_only_and_render_mixed_sources(self):
        self.fx.rich()
        # A credential FIFO would block forever if either dry-run read it.
        self.fx.credentials.unlink()
        before = self.fx.snapshot()
        fifo = self.fx.root / 'unreadable-credentials-fifo'
        os.mkfifo(fifo)
        for bash in BASHES:
            for script in ['morning-report', 'morning-triage']:
                with self.subTest(bash=bash, script=script):
                    text = self.ok(self.fx.run(script, bash=bash, NIGHT_BATCH_SLACK_ENV=str(fifo)))
                    self.assertIn('2026-09-08 and 2026-09-09', text)
                    if script == 'morning-report':
                        self.assertIn('🟢 1 passed gates · 🟡 1 need review · 🔴 1 guarded', text)
                        self.assertIn('https://example.invalid/preview', text)
                        self.assertIn('Fix "quoted" reminder', text)
                    else:
                        self.assertIn('Health: *PARTIAL*', text)
                        self.assertIn('bd-cleanup ran: 2/3', text)
                        self.assertIn('AestheticcNext-review (1)', text)
        fifo.unlink()
        self.assertEqual(before, self.fx.snapshot())
        self.assertEqual(self.fx.calls(), [])

    def test_legacy_guarded_only_first_morning_and_empty_arrays(self):
        self.fx.marker('AestheticcNext-legacy', day='2026-09-08')
        for bash in BASHES:
            report = self.ok(self.fx.run(bash=bash))
            self.assertIn('🔴 1 guarded', report)
            self.assertIn('AestheticcNext-legacy', report)
            triage = self.ok(self.fx.run('morning-triage', bash=bash))
            self.assertIn('Health: *BROKEN*', triage)

    def test_no_markers_and_historical_archives_are_not_resurrected(self):
        self.fx.marker('AestheticcNext-old', day='2026-04-27')
        for bash in BASHES:
            self.assertIn('No new completed', self.ok(self.fx.run(bash=bash)))
            self.assertIn('no DM', self.ok(self.fx.run('morning-triage', bash=bash)))
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertIn('No new completed', self.fx.calls()[0]['text'])

    def test_skipped_night_and_active_precedence(self):
        self.fx.marker('AestheticcNext-ready', 'guarded', '2026-09-08')
        self.fx.marker('AestheticcNext-ready', 'reviewed', '2026-09-09')
        self.fx.marker('AestheticcNext-ready', 'complete', content='1788909999\n')
        report = self.ok(self.fx.run())
        self.assertIn('🟢 1 passed gates · 🟡 0 need review · 🔴 0 guarded', report)
        self.assertEqual(report.count('https://example.invalid/pull/101'), 1)
        triage = self.ok(self.fx.run('morning-triage'))
        self.assertIn('ready to ship: 1', triage)

    def test_triage_all_diagnostic_arrays_empty(self):
        self.fx.marker('AestheticcNext-ready', 'complete')
        (self.fx.root / 'logs/AestheticcNext-ready.log').write_text('bd-cleanup: force-pushed\n')
        codex = self.fx.state / 'gate3/AestheticcNext-ready/codex.log'
        codex.parent.mkdir(parents=True)
        codex.write_text('PASS: no blockers\n')
        for bash in BASHES:
            text = self.ok(self.fx.run('morning-triage', bash=bash))
            self.assertIn('Health: *WORKED*', text)
            self.assertNotIn('Codex BLOCKERs', text)

    def test_night_archives_without_slack_then_morning_reports(self):
        marker = self.fx.marker('AestheticcNext-ready', 'complete')
        marker.with_suffix('.preview-url').write_text('status=ready\nurl=https://example.invalid/preview\n')
        self.ok(self.fx.run(args=('--archive-only',), FAKE_TODAY='2026-09-08'))
        self.assertFalse(marker.exists())
        todo = (self.fx.root / 'TODO.md').read_text()
        self.assertIn('https://example.invalid/preview', todo)
        self.assertEqual(self.fx.calls(), [])
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertEqual(len(self.fx.calls()), 1)
        self.assertIn('https://example.invalid/preview', self.fx.calls()[0]['text'])
        self.assertEqual((self.fx.root / 'TODO.md').read_text(), todo)
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertEqual(len(self.fx.calls()), 1)

    def test_success_receipts_and_late_marker_next_morning(self):
        self.fx.marker('AestheticcNext-ready', 'complete')
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.fx.marker('AestheticcNext-late')
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertEqual(len(self.fx.calls()), 1)
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1', FAKE_TODAY='2026-09-10'))
        second = self.fx.calls()[1]['text']
        self.assertIn('AestheticcNext-late', second)
        self.assertNotIn('AestheticcNext-ready', second)

    def test_delivery_failures_keep_exact_payload_for_retry(self):
        for script in ['morning-report', 'morning-triage']:
            for mode in ['transport', 'api', 'malformed']:
                with self.subTest(script=script, failure=mode):
                    fx = Fixture()
                    self.addCleanup(fx.close)
                    fx.rich()
                    result = fx.run(script, args=(), FAKE_TRANSPORT='1', FAKE_CURL_MODE=mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((fx.outbox(script) / 'sent.json').exists())
                    payload = (fx.outbox(script) / 'payload.json').read_text()
                    self.ok(fx.run(script, args=(), FAKE_TRANSPORT='1'))
                    self.assertEqual((fx.outbox(script) / 'payload.json').read_text(), payload)
                    self.assertTrue((fx.outbox(script) / 'sent.json').exists())
                    self.assertEqual(fx.calls()[0], fx.calls()[1])
                    self.ok(fx.run(script, args=(), FAKE_TRANSPORT='1'))
                    self.assertEqual(len(fx.calls()), 2)

    def test_missing_token_and_retry_outside_archive_window(self):
        self.fx.rich()
        self.fx.credentials.unlink()
        result = self.fx.run(args=(), FAKE_TRANSPORT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.fx.calls(), [])
        self.assertTrue((self.fx.outbox() / 'payload.json').exists())
        self.fx.credentials.write_text('SLACK_BOT_TOKEN="xoxb-FAKE-NOT-A-TOKEN"\n')
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1', FAKE_TODAY='2026-09-12'))
        self.assertIn('AestheticcNext-ready', self.fx.calls()[0]['text'])
        self.assertTrue((self.fx.outbox() / 'sent.json').exists())

    def test_failed_same_day_retry_keeps_payload_and_completes_local_work(self):
        first = self.fx.marker('AestheticcNext-ready', 'complete')
        self.fx.credentials.unlink()
        self.assertNotEqual(self.fx.run(args=(), FAKE_TRANSPORT='1').returncode, 0)
        payload = (self.fx.outbox() / 'payload.json').read_bytes()
        identities = (self.fx.outbox() / 'markers').read_bytes()
        self.assertFalse(first.exists())

        fresh = self.fx.marker('AestheticcNext-fresh')
        preview = fresh.with_suffix('.preview-url')
        preview.write_text('status=ready\nurl=https://example.invalid/fresh\n')
        alert = self.fx.state / 'zero-eligible-alert'
        alert.write_text('ALERT: fixture warning\n')
        self.fx.credentials.write_text('SLACK_BOT_TOKEN=""\n')
        self.assertNotEqual(self.fx.run(args=(), FAKE_TRANSPORT='1').returncode, 0)
        todo = (self.fx.root / 'TODO.md').read_text()
        self.assertEqual(todo.count('### 🟢 AestheticcNext-ready'), 1)
        self.assertEqual(todo.count('### 🔴 AestheticcNext-fresh'), 1)
        self.assertIn('https://example.invalid/fresh', todo)
        self.assertIn('ALERT: fixture warning', todo)
        for path in [first, fresh, preview, alert]:
            self.assertFalse(path.exists())
            self.assertTrue((self.fx.state / 'processed/2026-09-09' / path.name).exists())
        self.assertEqual((self.fx.outbox() / 'payload.json').read_bytes(), payload)
        self.assertEqual((self.fx.outbox() / 'markers').read_bytes(), identities)
        self.assertFalse((self.fx.outbox() / 'sent.json').exists())
        self.assertEqual(list(self.fx.state.rglob('.pending-*')), [])
        self.assertIn('ERROR: missing Slack token; retained',
                      (self.fx.root / 'logs/morning-report.log').read_text())
        self.assertEqual(self.fx.calls(), [])

        self.fx.credentials.write_text('SLACK_BOT_TOKEN="xoxb-FAKE-NOT-A-TOKEN"\n')
        self.assertIn('already delivered', self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1')))
        expected = json.loads(payload)
        self.assertIn('AestheticcNext-ready', expected['text'])
        self.assertNotIn('AestheticcNext-fresh', expected['text'])
        self.assertEqual(self.fx.calls(), [expected])
        self.assertTrue((self.fx.outbox() / 'sent.json').exists())
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertEqual(self.fx.calls(), [expected])
        self.assertEqual((self.fx.root / 'TODO.md').read_text(), todo)
        self.assertEqual(list(self.fx.state.rglob('.pending-*')), [])
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1', FAKE_TODAY='2026-09-10'))
        self.assertIn('AestheticcNext-fresh', self.fx.calls()[1]['text'])
        self.assertNotIn('AestheticcNext-ready', self.fx.calls()[1]['text'])

    def test_stuck_backlog_does_not_block_todo_or_archiving(self):
        for mode in ['missing', 'blank', 'mangled', 'transport', 'api', 'malformed']:
            with self.subTest(failure=mode):
                fx = Fixture()
                self.addCleanup(fx.close)
                fx.marker('AestheticcNext-backlog')
                if mode == 'missing':
                    fx.credentials.unlink()
                elif mode == 'blank':
                    fx.credentials.write_text('SLACK_BOT_TOKEN=""\n')
                elif mode == 'mangled':
                    # The transport stub rejects this fake token with a trailing quote.
                    fx.credentials.write_text('SLACK_BOT_TOKEN="xoxb-FAKE-NOT-A-TOKEN""\n')
                env = {'FAKE_TRANSPORT': '1', 'FAKE_CURL_MODE': mode}
                self.assertNotEqual(fx.run(args=(), FAKE_TODAY='2026-09-08', **env).returncode, 0)
                old_outbox = fx.outbox(day='2026-09-08')
                payload = (old_outbox / 'payload.json').read_bytes()
                fresh = fx.marker('AestheticcNext-fresh')
                self.assertNotEqual(fx.run(args=(), **env).returncode, 0)
                self.assertIn('### 🔴 AestheticcNext-fresh', (fx.root / 'TODO.md').read_text())
                self.assertFalse(fresh.exists())
                self.assertTrue((fx.state / 'processed/2026-09-09' / fresh.name).exists())
                self.assertEqual((old_outbox / 'payload.json').read_bytes(), payload)
                self.assertFalse((old_outbox / 'sent.json').exists())
                self.assertIn('AestheticcNext-fresh', (fx.outbox() / 'payload.json').read_text())
                self.assertFalse((fx.outbox() / 'sent.json').exists())
                self.assertEqual(list(fx.state.rglob('.pending-*')), [])
                self.assertIn('ERROR:', (fx.root / 'logs/morning-report.log').read_text())

    def test_concurrent_delivery_has_one_acknowledgement(self):
        self.fx.rich()
        env = self.fx.env | {'FAKE_TRANSPORT': '1', 'FAKE_CURL_DELAY': '1'}
        command = ['/bin/bash', str(SCRIPTS / 'morning-report.sh')]
        first = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not self.fx.calls() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.fx.calls())
            second = self.fx.run(args=(), FAKE_TRANSPORT='1')
            self.assertIn('already delivered', self.ok(second))
            stdout, stderr = first.communicate(timeout=20)
            self.assertEqual(first.returncode, 0, stdout + stderr)
            self.assertEqual(len(self.fx.calls()), 1)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()

    def test_morning_waits_for_overlapping_night_archive(self):
        self.fx.marker('AestheticcNext-ready', 'complete')
        env = self.fx.env | {'FAKE_LOOKUP_DELAY': '0.5'}
        command = ['/bin/bash', str(SCRIPTS / 'morning-report.sh'), '--archive-only']
        night = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.fx.root / 'lookup-started').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.fx.root / 'lookup-started').exists())
            self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
            stdout, stderr = night.communicate(timeout=20)
            self.assertEqual(night.returncode, 0, stdout + stderr)
            self.assertEqual(len(self.fx.calls()), 1)
            self.assertIn('AestheticcNext-ready', self.fx.calls()[0]['text'])
            self.assertEqual((self.fx.root / 'TODO.md').read_text().count('### 🟢'), 1)
        finally:
            if night.poll() is None:
                night.kill()
                night.communicate()

    def test_complete_without_pr_still_has_details(self):
        self.fx.marker('AestheticcNext-ready', 'complete', '2026-09-08')
        text = self.ok(self.fx.run(FAKE_NO_PRS='1'))
        self.assertIn('Fix "quoted" reminder (complete; no PR found)', text)

    def test_triage_reports_diff_churn(self):
        self.fx.marker('AestheticcNext-ready', 'complete')
        (self.fx.root / 'logs/AestheticcNext-ready.log').write_text('bd-cleanup: force-pushed\n')
        text = self.ok(self.fx.run('morning-triage', FAKE_DIRTY_PR='1'))
        self.assertIn('Health: *PARTIAL*', text)
        self.assertIn('PR #101 (AestheticcNext-ready) still has bd state churn', text)

    def test_zero_eligible_and_dreams_without_markers(self):
        (self.fx.state / 'zero-eligible-alert').write_text('ALERT: 3 nights with ZERO eligible beads\n')
        (self.fx.root / 'dreams/fixture-dream.md').write_text('# Fake dream\n')
        report = self.ok(self.fx.run())
        self.assertIn('ALERT: 3 nights', report)
        self.assertIn('fixture-dream.md', report)
        self.ok(self.fx.run(args=('--archive-only',), FAKE_TODAY='2026-09-08'))
        self.assertEqual(self.fx.calls(), [])
        self.assertIn('ALERT: 3 nights', self.ok(self.fx.run()))
        self.ok(self.fx.run(args=(), FAKE_TRANSPORT='1'))
        self.assertIn('ALERT: 3 nights', self.fx.calls()[0]['text'])

    def test_today_only_missing_yesterday_and_calendar_boundaries(self):
        self.fx.marker('AestheticcNext-today', day='2026-09-09')
        self.assertIn('AestheticcNext-today', self.ok(self.fx.run()))
        for today, yesterday in [('2026-03-30', '2026-03-29'), ('2026-10-26', '2026-10-25'),
                                 ('2027-01-01', '2026-12-31')]:
            self.fx.marker('AestheticcNext-calendar', day=yesterday)
            self.assertIn('AestheticcNext-calendar', self.ok(self.fx.run(FAKE_TODAY=today)))

    def test_todo_failure_does_not_consume_markers_or_send(self):
        marker = self.fx.marker('AestheticcNext-guard')
        result = self.fx.run(args=(), NIGHT_BATCH_TODO=str(self.fx.root / 'missing/TODO.md'))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(self.fx.calls(), [])


def transcripts():
    fx = Fixture()
    try:
        fx.rich()
        for script, clock in [('morning-report', '07:00'), ('morning-triage', '07:30')]:
            result = fx.run(script, FAKE_CLOCK=clock)
            assert result.returncode == 0 and not result.stderr, result
            print(f'$ /bin/bash hooks/night-batch/{script}.sh --dry-run [isolated fake environment]')
            print(result.stdout.rstrip())
            print()
        assert not fx.calls(), 'dry-run invoked curl'
        print('Safety: no curl calls; fake roots/credentials; bd, gh and git replaced by local stubs.')
    finally:
        fx.close()


if __name__ == '__main__':
    if '--transcripts' in sys.argv:
        transcripts()
    else:
        unittest.main(verbosity=2)
