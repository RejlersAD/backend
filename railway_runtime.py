"""Supervise the existing Railway web service and its explicitly enabled jobs.

This parent deliberately imports no Django application or configuration. Finance
runs once per service replica, outside Gunicorn workers, and its domain service
owns the database lease that coordinates overlapping replicas/deployments.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence


def runtime_commands(environ: Mapping[str, str]) -> list[tuple[str, list[str]]]:
    """Preserve the previous shell defaults, including empty-value fallback."""
    def value(name: str, default: str) -> str:
        return environ.get(name) or default

    commands = [('web', [
        'gunicorn', 'config.wsgi_bulletproof:application',
        '--bind', f"0.0.0.0:{value('PORT', '8000')}",
        '--workers', value('GUNICORN_WORKERS', '1'),
        '--threads', value('GUNICORN_THREADS', '4'),
        '--worker-class', value('GUNICORN_WORKER_CLASS', 'gthread'),
        '--timeout', value('GUNICORN_TIMEOUT', '2400'),
        '--graceful-timeout', value('GUNICORN_GRACEFUL_TIMEOUT', '30'),
        '--keep-alive', value('GUNICORN_KEEPALIVE', '75'),
        '--max-requests', value('GUNICORN_MAX_REQUESTS', '500'),
        '--max-requests-jitter', value('GUNICORN_MAX_REQUESTS_JITTER', '50'),
        '--log-file', '-', '--access-logfile', '-', '--error-logfile', '-',
        '--log-level', value('GUNICORN_LOG_LEVEL', 'info'),
        '--capture-output', '--enable-stdio-inheritance',
    ])]
    # Preserve the established, exact Celery enable switch.
    if environ.get('CELERY_WORKER_ENABLED') == 'true':
        commands.append(('celery', [
            'celery', '-A', 'config', 'worker',
            f"--loglevel={value('CELERY_LOG_LEVEL', 'info')}",
            f"--concurrency={value('CELERY_CONCURRENCY', '1')}",
            '--pool=prefork',
            f"--max-tasks-per-child={value('CELERY_MAX_TASKS_PER_CHILD', '100')}",
            '--without-heartbeat', '--without-mingle',
        ]))
    if environ.get('FINANCE_SHAREPOINT_SYNC_ENABLED', '').strip().lower() in {
        'true', '1', 'yes', 'on',
    }:
        commands.append(('finance', [sys.executable, '-m', 'apps.finance.sharepoint_runtime']))
    return commands


def shutdown_timeout(environ: Mapping[str, str]) -> float:
    """Allow the configured Gunicorn grace period, bounded to two minutes."""
    try:
        return max(1, min(120, int(environ.get('GUNICORN_GRACEFUL_TIMEOUT') or '30')))
    except (ValueError, TypeError):
        return 30


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    # A child may have exited while its workers are still alive. Signal its
    # original group regardless of the leader's return code.
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _group_alive(process: subprocess.Popen) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap_after_shutdown(processes: Sequence[subprocess.Popen]) -> None:
    for process in processes:
        process.poll()
    # When this parent is container PID 1, exited grandchildren are adopted by
    # it. Reap those too; their status does not override the triggering result.
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if not pid:
            break


def _stop_children(processes: Sequence[subprocess.Popen], signum: int, timeout: float) -> None:
    for process in processes:
        _signal_group(process, signum)
    deadline = time.monotonic() + timeout
    while True:
        _reap_after_shutdown(processes)
        if not any(_group_alive(process) for process in processes):
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    for process in processes:
        _signal_group(process, signal.SIGKILL)
    # SIGKILL cannot be ignored; bound reaping as well for uninterruptible I/O.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        _reap_after_shutdown(processes)
        if not any(_group_alive(process) for process in processes):
            break
        time.sleep(0.05)


def supervise(commands: Sequence[tuple[str, Sequence[str]]], *, grace_seconds: float = 30) -> int:
    """Exit when any required child exits; clean every remaining process group."""
    received_signal = None
    processes: list[tuple[str, subprocess.Popen]] = []

    def receive_signal(signum, _frame):
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum

    previous_handlers = {
        signum: signal.signal(signum, receive_signal)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        for name, command in commands:
            if received_signal is not None:
                break
            try:
                process = subprocess.Popen(command, start_new_session=True)
            except OSError:
                # Command/environment text can carry sensitive values.
                print(f'[RUNTIME] {name} could not start.', flush=True)
                return 1
            processes.append((name, process))
            print(f'[RUNTIME] {name} started.', flush=True)
        while received_signal is None:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f'[RUNTIME] {name} exited (status {code}).', flush=True)
                    # A normally stopped web server retains its old exit code.
                    # An enabled background service must never silently vanish.
                    if code < 0:
                        return 128 - code
                    return code if name == 'web' or code else 1
            time.sleep(0.1)
        return 128 + received_signal
    finally:
        _stop_children(
            [process for _, process in processes],
            received_signal or signal.SIGTERM,
            grace_seconds,
        )
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main() -> int:
    os.environ['DJANGO_SETTINGS_MODULE'] = os.environ.get('DJANGO_SETTINGS_MODULE') or 'config.settings'
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.environ['PORT'] = os.environ.get('PORT') or '8000'
    return supervise(runtime_commands(os.environ), grace_seconds=shutdown_timeout(os.environ))


if __name__ == '__main__':
    raise SystemExit(main())
