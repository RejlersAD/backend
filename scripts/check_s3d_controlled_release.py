"""Verify only local radai_dev; apply 0015/0016 and roll back synthetic probes."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SPEC_SKIP_CORS_ON_READY'] = '1'
os.environ['S3_AUTO_APPLY_CORS'] = '0'
os.environ['USE_S3'] = 'false'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.db import connection, transaction
from django.test import override_settings
from unittest.mock import patch
from apps.spec_customization.models import PaperSpecDocument
from apps.spec_customization.tests_matching_pipeline import RemoteMemoryStorage
from django.db.migrations.executor import MigrationExecutor
from apps.spec_customization.tests_controlled_release import ControlledReleaseTests

assert connection.vendor == 'postgresql'
assert connection.settings_dict['NAME'] == 'radai_dev'
assert connection.settings_dict['HOST'] in {'postgres_local', 'radai_postgres_local', 'localhost', '127.0.0.1'}
target = [('spec_customization', '0016_import_pilot')]
executor = MigrationExecutor(connection)
plan = executor.migration_plan(target)
assert all(m.app_label == 'spec_customization' and m.name in {'0015_controlled_release', '0016_import_pilot'} and not reverse for m, reverse in plan)
if plan:
    assert '--apply' in sys.argv, 'Only 0015/0016 may be applied; pass --apply explicitly.'
    executor.migrate(target)
assert not MigrationExecutor(connection).migration_plan(target)
print('PASS: local radai_dev migrations 0015/0016 applied.', flush=True)

methods = [
    'test_validate_freeze_and_released_bytes_are_reproducible',
    'test_cell_override_invalidates_without_mutating_historical_release',
    'test_import_report_is_persisted_with_ground_truth_and_reconciliation',
    'test_pilot_rejects_unconfigured_target_and_missing_report',
    'test_unauthorized_release_and_pilot_access',
    'test_reviewed_codes_reach_real_preview_and_export',
]
for method in methods:
    case = ControlledReleaseTests(method)
    try:
        with transaction.atomic(), override_settings(ROOT_URLCONF='config.urls_release_test', MIDDLEWARE=['django.contrib.sessions.middleware.SessionMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware']):
            case.enterContext(patch.object(PaperSpecDocument._meta.get_field('file'), 'storage', RemoteMemoryStorage()))
            case.setUp()
            getattr(case, method)()
            transaction.set_rollback(True)
    finally:
        case.doCleanups()
    print('PASS:', method, '(synthetic rows rolled back)', flush=True)
print('No authority/reference/target configuration was persisted; no SP3D import or production change was performed.', flush=True)
