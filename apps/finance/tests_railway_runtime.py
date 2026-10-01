"""Stdlib-only runtime regressions, including real harmless Linux child groups.

Run with ``python -m unittest apps.finance.tests_railway_runtime``. No settings,
database, Graph client or application process is loaded by this suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from railway_runtime import runtime_commands, shutdown_timeout


class RuntimeCommandsTests(unittest.TestCase):
    def test_disabled_background_services_leave_only_existing_web_command(self):
        commands = runtime_commands({})
        self.assertEqual(commands, [('web', [
            'gunicorn', 'config.wsgi_bulletproof:application',
            '--bind', '0.0.0.0:8000', '--workers', '1', '--threads', '4',
            '--worker-class', 'gthread', '--timeout', '2400',
            '--graceful-timeout', '30', '--keep-alive', '75',
            '--max-requests', '500', '--max-requests-jitter', '50',
            '--log-file', '-', '--access-logfile', '-', '--error-logfile', '-',
            '--log-level', 'info', '--capture-output', '--enable-stdio-inheritance',
        ])])

    def test_finance_switch_accepts_configured_truthy_values_only(self):
        for enabled in ('true', 'TRUE', '1', 'yes', 'on', ' On '):
            with self.subTest(enabled=enabled):
                commands = runtime_commands({'FINANCE_SHAREPOINT_SYNC_ENABLED': enabled})
                self.assertEqual([name for name, _ in commands], ['web', 'finance'])
                self.assertEqual(commands[-1][1], [
                    sys.executable, '-m', 'apps.finance.sharepoint_runtime',
                ])
        for disabled in ('', 'false', '0', 'no', 'off', 'unexpected'):
            with self.subTest(disabled=disabled):
                self.assertEqual(len(runtime_commands({
                    'FINANCE_SHAREPOINT_SYNC_ENABLED': disabled,
                })), 1)

    def test_existing_gunicorn_and_celery_overrides_are_preserved(self):
        environment = {
            'PORT': '9001', 'GUNICORN_WORKERS': '3', 'GUNICORN_THREADS': '8',
            'GUNICORN_WORKER_CLASS': 'sync', 'GUNICORN_TIMEOUT': '90',
            'GUNICORN_GRACEFUL_TIMEOUT': '45', 'GUNICORN_KEEPALIVE': '9',
            'GUNICORN_MAX_REQUESTS': '700', 'GUNICORN_MAX_REQUESTS_JITTER': '70',
            'GUNICORN_LOG_LEVEL': 'warning', 'CELERY_WORKER_ENABLED': 'true',
            'CELERY_LOG_LEVEL': 'error', 'CELERY_CONCURRENCY': '2',
            'CELERY_MAX_TASKS_PER_CHILD': '50', 'FINANCE_SHAREPOINT_SYNC_ENABLED': 'true',
        }
        commands = runtime_commands(environment)
        self.assertEqual([name for name, _ in commands], ['web', 'celery', 'finance'])
        web = commands[0][1]
        for flag, expected in {
            '--bind': '0.0.0.0:9001', '--workers': '3', '--threads': '8',
            '--worker-class': 'sync', '--timeout': '90', '--graceful-timeout': '45',
            '--keep-alive': '9', '--max-requests': '700',
            '--max-requests-jitter': '70', '--log-level': 'warning',
        }.items():
            self.assertEqual(web[web.index(flag) + 1], expected)
        self.assertEqual(commands[1][1], [
            'celery', '-A', 'config', 'worker', '--loglevel=error', '--concurrency=2',
            '--pool=prefork', '--max-tasks-per-child=50',
            '--without-heartbeat', '--without-mingle',
        ])

    def test_celery_retains_exact_legacy_enablement_and_empty_fallback(self):
        for disabled in ('TRUE', '1', 'yes', 'false', ''):
            self.assertEqual(len(runtime_commands({'CELERY_WORKER_ENABLED': disabled})), 1)
        defaults = runtime_commands({})
        self.assertEqual(defaults, runtime_commands({'PORT': '', 'GUNICORN_WORKERS': ''}))
        celery = runtime_commands({'CELERY_WORKER_ENABLED': 'true', 'CELERY_CONCURRENCY': ''})[1][1]
        self.assertIn('--concurrency=1', celery)

    def test_shutdown_grace_is_bounded_and_tolerates_invalid_values(self):
        self.assertEqual(shutdown_timeout({}), 30)
        for setting, expected in (('45', 45), ('bad', 30), ('', 30), ('0', 1), ('9999', 120)):
            self.assertEqual(shutdown_timeout({'GUNICORN_GRACEFUL_TIMEOUT': setting}), expected)


CHILD_PROGRAM = r'''
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

directory, name, mode, status = sys.argv[1:]
folder = Path(directory)
descendant = None

def stop(signum, frame):
    (folder / (name + '.signal')).write_text(str(signum))
    if mode == 'ignore':
        return
    if descendant is not None:
        try:
            descendant.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
(folder / (name + '.pid')).write_text(str(os.getpid()))
if mode in ('tree', 'orphan'):
    descendant = subprocess.Popen([sys.executable, __file__, directory, name + '-descendant', 'wait', '0'])
    while not (folder / (name + '-descendant.pid')).exists():
        time.sleep(0.01)
(folder / (name + '.ready')).touch()
if mode in ('exit', 'orphan'):
    while not (folder / 'release-exit').exists():
        time.sleep(0.01)
    raise SystemExit(int(status))
while True:
    time.sleep(0.05)
'''

SUPERVISOR_PROGRAM = '''
import json
import sys
from railway_runtime import supervise
raise SystemExit(supervise(json.loads(sys.argv[1]), grace_seconds=float(sys.argv[2])))
'''


@unittest.skipUnless(os.name == 'posix', 'Railway process groups require Linux/POSIX')
class RuntimeProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.child_file = self.folder / 'child.py'
        self.child_file.write_text(CHILD_PROGRAM)
        self.process = None
        self.addCleanup(self.stop_test_processes)

    def child(self, name, mode='wait', status=0):
        return (name, [sys.executable, str(self.child_file), str(self.folder), name, mode, str(status)])

    def start(self, commands, grace=0.4):
        root = Path(__file__).resolve().parents[2]
        self.process = subprocess.Popen(
            [sys.executable, '-c', SUPERVISOR_PROGRAM, json.dumps(commands), str(grace)],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )

    def ready(self, *names):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all((self.folder / (name + '.ready')).exists() for name in names):
                return
            if self.process.poll() is not None:
                self.fail(self.process.communicate()[0])
            time.sleep(0.01)
        self.fail('Synthetic runtime children did not start.')

    def finish(self, expected):
        output, _ = self.process.communicate(timeout=8)
        self.assertEqual(self.process.returncode, expected, output)
        for marker in self.folder.glob('*.pid'):
            pid = int(marker.read_text())
            stat = Path(f'/proc/{pid}/stat')
            if stat.exists():
                self.assertEqual(stat.read_text().split()[2], 'Z', f'{marker.stem} still running')
        return output

    def stop_test_processes(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.communicate(timeout=4)
        # Prevent a failing regression from leaving synthetic test helpers alive.
        for marker in self.folder.glob('*.pid'):
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_sigterm_reaches_web_finance_worker_and_worker_descendant(self):
        self.start([self.child('web'), self.child('celery', 'tree'), self.child('finance')])
        self.ready('web', 'celery', 'finance', 'celery-descendant')
        self.process.send_signal(signal.SIGTERM)
        self.finish(128 + signal.SIGTERM)
        for name in ('web', 'celery', 'finance', 'celery-descendant'):
            self.assertEqual(int((self.folder / (name + '.signal')).read_text()), signal.SIGTERM)

    def test_sigint_is_forwarded(self):
        self.start([self.child('web'), self.child('finance')])
        self.ready('web', 'finance')
        self.process.send_signal(signal.SIGINT)
        self.finish(128 + signal.SIGINT)
        self.assertEqual(int((self.folder / 'finance.signal').read_text()), signal.SIGINT)

    def test_finance_failure_stops_siblings_and_propagates_failure(self):
        self.start([self.child('web'), self.child('celery'), self.child('finance', 'exit', 7)])
        self.ready('web', 'celery', 'finance')
        (self.folder / 'release-exit').touch()
        self.finish(7)
        self.assertTrue((self.folder / 'web.signal').exists())
        self.assertTrue((self.folder / 'celery.signal').exists())

    def test_unexpected_background_success_also_restarts_service(self):
        self.start([self.child('web'), self.child('finance', 'exit', 0)])
        self.ready('web', 'finance')
        (self.folder / 'release-exit').touch()
        self.finish(1)

    def test_web_exit_status_is_preserved_and_finance_is_stopped(self):
        for status in (0, 4):
            with self.subTest(status=status):
                # Separate markers prevent the second synthetic child from exiting early.
                (self.folder / 'release-exit').unlink(missing_ok=True)
                for marker in self.folder.glob('*.ready'):
                    marker.unlink()
                self.start([self.child('web', 'exit', status), self.child('finance')])
                self.ready('web', 'finance')
                (self.folder / 'release-exit').touch()
                self.finish(status)
                self.assertTrue((self.folder / 'finance.signal').exists())

    def test_failed_spawn_cleans_started_web_without_logging_arguments(self):
        self.start([self.child('web'), ('finance', ['/missing/synthetic-secret-value'])])
        output = self.finish(1)
        self.assertIn('finance could not start', output)
        self.assertNotIn('synthetic-secret-value', output)

    def test_unresponsive_child_is_killed_after_grace_period(self):
        self.start([self.child('web'), self.child('finance', 'ignore')], grace=0.2)
        self.ready('web', 'finance')
        started = time.monotonic()
        self.process.send_signal(signal.SIGTERM)
        self.finish(128 + signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue((self.folder / 'finance.signal').exists())

    def test_exited_leader_does_not_leave_its_descendant_running(self):
        self.start([self.child('web'), self.child('finance', 'orphan', 5)], grace=0.2)
        self.ready('web', 'finance', 'finance-descendant')
        (self.folder / 'release-exit').touch()
        self.finish(5)
        self.assertTrue((self.folder / 'finance-descendant.signal').exists())


if __name__ == '__main__':
    unittest.main()
