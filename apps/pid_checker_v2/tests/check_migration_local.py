"""Apply Task 2 and Task 3 DDL on disposable SQLite after model sync.

Run only with config.settings_pid_checker_security_test. Existing app migrations
include PostgreSQL SQL, so their historical state is faked on this disposable
database after model-sync; 0027 and 0028 are applied normally.
"""
from django.core.management import call_command
from django.apps import apps
from django.db import connection
from django.test import override_settings

from apps.pid_checker_v2.models import (CRSChainProjectBinding,
                                        PidEngineeringEvidenceLink,
                                        PidEvidenceReviewEvent)


with override_settings(MIGRATION_MODULES={app.label: None for app in apps.get_app_configs()}):
    call_command('migrate', run_syncdb=True, verbosity=0, interactive=False)
with connection.schema_editor() as editor:
    editor.delete_model(PidEvidenceReviewEvent)
    editor.delete_model(PidEngineeringEvidenceLink)
    editor.delete_model(CRSChainProjectBinding)
with override_settings(MIGRATION_MODULES={}):
    from django.db.migrations.recorder import MigrationRecorder
    call_command('migrate', 'pid_checker_v2', '0027', fake=True, verbosity=0, interactive=False)
    MigrationRecorder(connection).record_unapplied('pid_checker_v2', '0027_engineering_evidence_links')
    call_command('migrate', 'pid_checker_v2', '0027', verbosity=0, interactive=False)
    call_command('migrate', 'pid_checker_v2', '0028', verbosity=0, interactive=False)
tables = set(connection.introspection.table_names())
assert CRSChainProjectBinding._meta.db_table in tables
assert PidEngineeringEvidenceLink._meta.db_table in tables
assert PidEvidenceReviewEvent._meta.db_table in tables
with connection.cursor() as cursor:
    columns = {column.name for column in connection.introspection.get_table_description(
        cursor, PidEngineeringEvidenceLink._meta.db_table,
    )}
assert {'review_state', 'evidence_fingerprint', 'reviewer_id', 'line_list_id'} <= columns
print('Task 2 migration 0027 and Task 3 migration 0028 applied on disposable SQLite')
