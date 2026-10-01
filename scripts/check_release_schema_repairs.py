"""Actual forward repair DDL on minimal synthetic PostgreSQL dependency tables.

Never accesses application data. This verifies the two release repairs and
retained-table compatibility, not the entire historical migration chain.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys

from check_document_control_migrations import provision_database


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_procurement_postgresql_test'
    os.environ.setdefault('RADAI_DOCUMENT_MIGRATION_DATABASE', 'document_control_migration_verify_repairs')
    database = provision_database()
    from django.conf import settings
    settings.DATABASES['default']['NAME'] = database
    settings.DATABASES['default']['TEST']['NAME'] = database
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, 'apps.pid_verification_v2']
    import django
    django.setup()
    from django.apps import apps
    from django.db import connection, transaction
    from django.db.migrations.exceptions import IrreversibleError
    from django.db.migrations.state import ModelState, ProjectState
    from config.migration_schema import ensure_declared_table

    assert connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    if connection.introspection.table_names():
        raise RuntimeError('Refusing a populated verification database; use a fresh synthetic name.')
    baseline = ProjectState()
    references = (('users', 'User'), ('finance', 'PayrollRun'), ('pid_verification_v2', 'PIDVDocument'))
    for app_label, name in references:
        source = apps.get_model(app_label, name)
        baseline.add_model(ModelState(app_label, name, [(source._meta.pk.name, source._meta.pk.clone())],
                                      options={'db_table': source._meta.db_table}))
    for name in ('PayrollWorkflow', 'WorkflowNotificationLog'):
        baseline.add_model(ModelState.from_model(apps.get_model('finance', name)))
    with connection.schema_editor() as editor:
        for app_label, name in references:
            editor.create_model(baseline.apps.get_model(app_label, name))

    finance = importlib.import_module('apps.finance.migrations.0017_restore_missing_payroll_workflow_tables')
    pid = importlib.import_module('apps.pid_verification_v2.migrations.0006_pidvtagindex')
    migrations = [finance.Migration('0017_restore_missing_payroll_workflow_tables', 'finance'),
                  pid.Migration('0006_pidvtagindex', 'pid_verification_v2')]
    state = baseline.clone()
    before = []
    for migration in migrations:
        before.append(state.clone())
        with connection.schema_editor() as editor:
            state = migration.apply(state, editor)
    print('PASS: missing Finance workflow/log and P&ID V2 tag tables created by actual forward operations', flush=True)
    actor = state.apps.get_model('users', 'User').objects.create()
    payroll = state.apps.get_model('finance', 'PayrollRun').objects.create()
    document = state.apps.get_model('pid_verification_v2', 'PIDVDocument').objects.create()
    workflow_model = state.apps.get_model('finance', 'PayrollWorkflow')
    notification_model = state.apps.get_model('finance', 'WorkflowNotificationLog')
    tag_model = state.apps.get_model('pid_verification_v2', 'PIDVTagIndex')
    workflow = workflow_model.objects.create(payroll_run_id=payroll.pk, submitted_by_id=actor.pk)
    notification_model.objects.create(workflow_id=workflow.pk, recipient_email='synthetic@example.test',
                                      subject='Retained audit', message_body='Original content')
    tag_model.objects.create(document_id=document.pk, page_number=1, tag='TAG-1', raw_tag='Tag-1', tag_type='line')
    models = (workflow_model, notification_model, tag_model)
    snapshots = {model._meta.db_table: list(model.objects.values()) for model in models}
    for migration, starting_state in zip(migrations, before):
        with connection.schema_editor() as editor:
            migration.apply(starting_state.clone(), editor)
    for model in models:
        assert list(model.objects.values()) == snapshots[model._meta.db_table]
        with connection.schema_editor() as editor:
            ensure_declared_table(model, editor)
    print('PASS: compatible populated tables retained byte-for-field; PK/FK/unique/index/type/nullability/check verified', flush=True)

    # A partial previously created table must stop the migration, not be silently adopted.
    failures = [
        ['ALTER TABLE finance_workflow_notification_log DROP COLUMN message_body'],
        ['ALTER TABLE pidv2_tag_index ALTER COLUMN tag TYPE varchar(10)'],
        ['ALTER TABLE pidv2_tag_index ALTER COLUMN raw_tag DROP NOT NULL'],
        ['ALTER TABLE pidv2_tag_index ALTER COLUMN id DROP IDENTITY'],
    ]
    with connection.cursor() as cursor:
        checks = connection.introspection.get_constraints(cursor, 'pidv2_tag_index')
    check_name = next(name for name, item in checks.items()
                      if item.get('check') and item['columns'] == ['page_number'])
    failures.append([
        f'ALTER TABLE pidv2_tag_index DROP CONSTRAINT {connection.ops.quote_name(check_name)}',
        'ALTER TABLE pidv2_tag_index ADD CONSTRAINT wrong_page_check CHECK (page_number >= -1)',
    ])
    for statements in failures:
        with transaction.atomic():
            with connection.cursor() as cursor:
                for sql in statements:
                    cursor.execute(sql)
            try:
                with connection.schema_editor() as editor:
                    for model in models:
                        ensure_declared_table(model, editor)
            except RuntimeError as exc:
                assert 'incompatible' in str(exc)
            else:
                raise AssertionError('An incompatible retained table was accepted.')
            transaction.set_rollback(True)
    print('PASS: missing columns, wrong types/nullability, missing identity and wrong positive check fail closed', flush=True)
    for migration, starting_state in zip(migrations, before):
        try:
            with connection.schema_editor() as editor:
                migration.unapply(starting_state.clone(), editor)
        except IrreversibleError:
            pass
        else:
            raise AssertionError('A repair must not drop a possibly preexisting table.')
    for model in models:
        assert list(model.objects.values()) == snapshots[model._meta.db_table]
    print('PASS: reverse attempts refuse deletion; all synthetic evidence remains unchanged', flush=True)
    print('BOUNDARY: focused additive repair DDL; no production or full-history replay claim', flush=True)


if __name__ == '__main__':
    main()
