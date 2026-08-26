"""Deployment and API contract checks for the planning runtime."""
from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import NoReverseMatch, reverse

from ..models import PlanningGeneration, PlanningJob, ScheduleAssuranceReview, ScheduleResource


REQUIRED_ROUTES = {
    'generate': ('planning_intelligence:planning-project-generate', [1]),
    'generation_preview': ('planning_intelligence:planning-project-generation-preview', [1]),
    'job_detail': ('planning_intelligence:planning-job-detail', [1]),
    'calculate': ('planning_intelligence:schedule-version-calculate', [1]),
    'run_assurance': ('planning_intelligence:schedule-version-run-assurance', [1]),
    'approve_assurance': ('planning_intelligence:schedule-version-approve-assurance', [1]),
    'baseline': ('planning_intelligence:schedule-version-baseline', [1]),
}


def check_planning_compatibility(*, require_worker=False):
    checks = []
    for code, (name, args) in REQUIRED_ROUTES.items():
        try:
            path = reverse(name, args=args)
            checks.append({'code': f'route_{code}', 'status': 'pass', 'detail': path})
        except NoReverseMatch as exc:
            checks.append({'code': f'route_{code}', 'status': 'fail', 'detail': str(exc)})

    required_fields = {
        PlanningJob: {'idempotency_key', 'progress_log', 'heartbeat_at', 'attempt_count'},
        PlanningGeneration: {'input_fingerprint'},
        ScheduleResource: {'capacity_units_per_day'},
        ScheduleAssuranceReview: {'calculated_state_at', 'input_fingerprint', 'blockers', 'status'},
    }
    for model, expected in required_fields.items():
        actual = {field.name for field in model._meta.fields}
        missing = sorted(expected - actual)
        checks.append({
            'code': f'model_{model._meta.model_name}', 'status': 'fail' if missing else 'pass',
            'detail': f'Missing fields: {", ".join(missing)}' if missing else 'Required fields available',
        })

    required_migrations = {
        ('planning_intelligence', '0021_trustworthy_generation'),
        ('planning_intelligence', '0022_trustworthy_scheduling'),
        ('planning_intelligence', '0023_operational_reliability'),
        ('planning_intelligence', '0024_assurance_input_fingerprint'),
    }
    migration_modules = getattr(settings, 'MIGRATION_MODULES', {})
    migrations_disabled = (
        'planning_intelligence' in migration_modules
        and migration_modules['planning_intelligence'] is None
    )
    if migrations_disabled:
        checks.append({
            'code': 'database_migrations', 'status': 'pass',
            'detail': 'Migration history check skipped by the isolated test settings',
        })
    else:
        executor = MigrationExecutor(connection)
        applied = set(executor.loader.applied_migrations)
        missing = sorted(required_migrations - applied)
        checks.append({
            'code': 'database_migrations', 'status': 'fail' if missing else 'pass',
            'detail': f'Missing: {missing}' if missing else 'Phase 2-4 migrations applied',
        })
    if require_worker:
        try:
            from config.celery import app
            replies = app.control.ping(timeout=3)
            checks.append({
                'code': 'celery_worker', 'status': 'pass' if replies else 'fail',
                'detail': f'{len(replies)} worker(s) responded' if replies else 'No Celery worker responded',
            })
        except Exception as exc:  # noqa: BLE001
            checks.append({'code': 'celery_worker', 'status': 'fail', 'detail': str(exc)})
    return {'compatible': all(row['status'] == 'pass' for row in checks), 'checks': checks}
