"""Execute the six shared-record migrations on a disposable PostgreSQL database.

This verifies the additive operations against a model-synchronized preceding
schema, not the repository's entire historical migration chain. It never opens
the running application's database and refuses to reuse a populated database.

Set RADAI_CONCURRENCY_PG_PORT and RADAI_CONCURRENCY_PG_PASSWORD for the explicitly
provisioned disposable server. Optionally set RADAI_SHARED_MIGRATION_DATABASE to
a fresh name beginning with ``shared_record_migration_verify``. No database is
dropped. Retain the final schema and synthetic fixtures for inspection.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import date
from decimal import Decimal
from uuid import uuid4


MIGRATIONS = [
    ('core', '0014_shared_record_identity'),
    ('invoice_tracker', '0006_canonical_invoice_references'),
    ('finance', '0016_receivables_source_identity'),
    ('planning_intelligence', '0049_resource_employee_identity'),
    ('project_control', '0011_hour_employee_identity'),
    ('project_organizer', '0003_enterprise_project_identity'),
]
ADDED_FIELDS = [
    ('core', 'project', 'client'),
    ('invoice_tracker', 'customerinvoice', 'canonical_project'),
    ('invoice_tracker', 'customerinvoice', 'canonical_client'),
    ('invoice_tracker', 'customerinvoice', 'canonical_identity_basis'),
    ('planning_intelligence', 'scheduleresource', 'employee'),
    ('project_control', 'approvedhourentry', 'employee'),
    ('project_organizer', 'project', 'enterprise_project'),
]
ADDED_MODELS = [('finance', 'receivablessourceidentity'), ('core', 'sharedrecordlinkcommand')]
SOURCE_MODELS = [
    ('core', 'Project'), ('sales', 'Client'), ('hr_core', 'EmployeeMaster'),
    ('planning_intelligence', 'PlanningProject'), ('planning_intelligence', 'ScheduleResource'),
    ('project_control', 'ApprovedHourEntry'), ('project_organizer', 'Project'),
    ('invoice_tracker', 'CustomerInvoice'), ('finance', 'ReceivablesSourceSnapshot'),
    ('finance', 'ReceivablesSourceRow'),
]


def provision_database():
    """Only create a distinctly named DB on the explicit synthetic test server."""
    import psycopg2
    from psycopg2 import sql

    port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
    password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
    database = os.environ.get('RADAI_SHARED_MIGRATION_DATABASE', 'shared_record_migration_verify')
    if not re.fullmatch(r'shared_record_migration_verify(?:_[a-z0-9]+)?', database):
        raise RuntimeError('The migration check requires its dedicated synthetic database name.')
    if not port.isdecimal() or not 1024 <= int(port) <= 65535 or int(port) == 5432 or not password:
        raise RuntimeError('Explicit disposable PostgreSQL port and password are required.')
    admin = psycopg2.connect(host='127.0.0.1', port=port, dbname='postgres',
                            user='radai_pr_concurrency', password=password, connect_timeout=5)
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [database])
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    finally:
        admin.close()
    return database


def seed_legacy(app_registry):
    def model(app, name):
        return app_registry.get_model(app, name)

    actor = model('users', 'User').objects.create(username='migration-reviewer', email='migration-reviewer@example.test')
    client = model('sales', 'Client').objects.create(client_code='MIG-CLI', company_name='Canonical synthetic client', account_manager_id=actor.pk)
    employee = model('hr_core', 'EmployeeMaster').objects.create(
        user_id=actor.pk, employee_number='MIG-EMP', employee_code='MIG-EMP', emp_code='MIG-EMP',
        first_name='Synthetic', last_name='Employee', email=actor.email, join_date=date(2020, 1, 1),
    )
    project = model('core', 'Project').objects.create(code='MIG-PRJ', name='Original project',
        client_name='Original client spelling', owner_id=actor.pk, contract_value=Decimal('123456.78'))
    workspace = model('planning_intelligence', 'PlanningProject').objects.create(
        name='Original planning workspace', client='Original planning client',
        enterprise_project_id=project.pk, created_by_id=actor.pk)
    resource = model('planning_intelligence', 'ScheduleResource').objects.create(
        project_id=workspace.pk, code='ROLE-OLD', name='Original role', unit_cost=Decimal('43.21'))
    wbs = model('project_control', 'WBSNode').objects.create(project_id=project.pk, code='1', name='Engineering')
    account = model('project_control', 'ControlAccount').objects.create(project_id=project.pk,
        wbs_node_id=wbs.pk, code='CA-1', name='Engineering', manager_id=actor.pk, status='active',
        baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 1, 31))
    period = model('project_control', 'ReportingPeriod').objects.create(project_id=project.pk,
        sequence=1, name='January', start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), data_date=date(2026, 1, 31))
    hour = model('project_control', 'ApprovedHourEntry').objects.create(project_id=project.pk,
        control_account_id=account.pk, reporting_period_id=period.pk, employee_code='LEGACY-PERSON',
        employee_name='Original employee spelling', work_date=date(2026, 1, 15), hours=Decimal('7.25'),
        hourly_cost_rate=Decimal('40.00'), labor_actual_cost=Decimal('290.00'), currency='AED',
        status='approved', source_reference='MIG-HOURS', approved_by_id=actor.pk)
    organizer = model('project_organizer', 'Project').objects.create(name='Original tool workspace',
        code='TOOL-LEGACY', client='Original tool client', created_by_id=actor.pk)
    invoice = model('invoice_tracker', 'CustomerInvoice').objects.create(invoice_number='MIG-INV',
        company='Original invoice company', account='Original account', rad_project_no='MIG-PRJ',
        project_id='EXTERNAL-KEY', project_name='Original invoice project', invoice_date=date(2026, 1, 3),
        invoice_amount=Decimal('123.45'), grand_total=Decimal('129.62'), currency='AED')
    snapshot = model('finance', 'ReceivablesSourceSnapshot').objects.create(sha256='a' * 64,
        file_name='synthetic-preservation.xlsx', sheet_name='Original sheet', last_row=6,
        row_count=1, is_active=True, reconciliation={'source': 'synthetic unchanged evidence'})
    source = model('finance', 'ReceivablesSourceRow').objects.create(snapshot_id=snapshot.pk, row_number=6,
        invoice_number='MIG-SOURCE', company='Original workbook company', account='Original workbook account',
        project_name='Original workbook project', rad_project_no='MIG-PRJ', project_id='SOURCE-KEY',
        invoice_date=date(2026, 1, 3), invoice_amount=Decimal('9876.54321098'),
        balance_to_be_received=Decimal('9876.54321098'), currency='AED', remarks='Original source fact')
    return {'actor': actor.pk, 'client': client.pk, 'employee': employee.pk, 'project': project.pk,
            'workspace': workspace.pk, 'resource': resource.pk, 'hour': hour.pk, 'organizer': organizer.pk,
            'invoice': invoice.pk, 'snapshot': snapshot.pk, 'source': source.pk}


def source_fingerprints(registry, source_fields):
    from django.core.serializers.json import DjangoJSONEncoder
    return {f'{app}.{name}': hashlib.sha256(json.dumps(
        list(registry.get_model(app, name).objects.order_by('pk').values(*source_fields[(app, name)])),
        cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest() for app, name in SOURCE_MODELS}


def verify_constraints(connection, registry):
    required = [
        ('core', 'Project', 'client_id'),
        ('invoice_tracker', 'CustomerInvoice', 'canonical_project_id'),
        ('invoice_tracker', 'CustomerInvoice', 'canonical_client_id'),
        ('finance', 'ReceivablesSourceIdentity', 'source_row_id'),
        ('finance', 'ReceivablesSourceIdentity', 'canonical_project_id'),
        ('finance', 'ReceivablesSourceIdentity', 'canonical_client_id'),
        ('planning_intelligence', 'ScheduleResource', 'employee_id'),
        ('project_control', 'ApprovedHourEntry', 'employee_id'),
    ]
    with connection.cursor() as cursor:
        for app, name, column in required:
            constraints = connection.introspection.get_constraints(cursor, registry.get_model(app, name)._meta.db_table)
            assert any(item['columns'] == [column] and item.get('foreign_key') for item in constraints.values()), (app, name, column)
        organizer_constraints = connection.introspection.get_constraints(cursor, registry.get_model('project_organizer', 'Project')._meta.db_table)
        assert not any(item['columns'] == ['enterprise_project_id'] and item.get('foreign_key') for item in organizer_constraints.values())
        command_constraints = connection.introspection.get_constraints(cursor, registry.get_model('core', 'SharedRecordLinkCommand')._meta.db_table)
        assert command_constraints['core_record_link_request']['unique']
        assert command_constraints['core_record_link_request']['columns'] == ['actor_id', 'request_id']


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_procurement_postgresql_test'
    database = provision_database()
    from django.conf import settings
    settings.DATABASES['default']['NAME'] = database
    settings.DATABASES['default']['TEST']['NAME'] = database
    import django
    django.setup()
    from django.apps import apps
    from django.core.management import call_command
    from django.db import connection, IntegrityError, transaction
    from django.db.migrations.state import ProjectState

    assert connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    assert connection.vendor == 'postgresql'
    with connection.cursor() as cursor:
        if connection.introspection.table_names(cursor):
            raise RuntimeError('Refusing a populated database. Select a fresh dedicated synthetic database name.')
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    baseline = ProjectState.from_apps(apps)
    for app, model, field in ADDED_FIELDS:
        baseline.remove_field(app, model, field)
    for app, model in ADDED_MODELS:
        baseline.remove_model(app, model)
    migrations, before, state = [], {}, baseline.clone()
    for app, name in MIGRATIONS:
        migration = importlib.import_module(f'apps.{app}.migrations.{name}').Migration(name, app)
        migrations.append(migration)
        before[(app, name)] = state.clone()
        state = migration.mutate_state(state)
    for migration in reversed(migrations):
        with connection.schema_editor() as editor:
            migration.unapply(before[(migration.app_label, migration.name)].clone(), editor)
    print('PASS: all six actual reverse operations executed on empty identity columns/tables.')

    references = seed_legacy(baseline.apps)
    fields = {(app, name): [field.attname for field in baseline.apps.get_model(app, name)._meta.local_fields]
              for app, name in SOURCE_MODELS}
    original = source_fingerprints(baseline.apps, fields)
    state = baseline.clone()
    for migration in migrations:
        with connection.schema_editor() as editor:
            state = migration.apply(state, editor)
        print(f'PASS: applied {migration.app_label}.{migration.name}')
    assert source_fingerprints(state.apps, fields) == original, 'Existing source facts changed during migration.'
    for app, model, field in ADDED_FIELDS:
        values = list(state.apps.get_model(app, model).objects.values_list(field, flat=True))
        assert all(value == ({} if field == 'canonical_identity_basis' else None) for value in values), (app, model, field)
    verify_constraints(connection, state.apps)
    print('PASS: ten source record types preserve every original field; seven new fields have null/empty defaults; eight database FKs and retry uniqueness verified.')

    model = state.apps.get_model
    model('core', 'Project').objects.filter(pk=references['project']).update(client_id=references['client'])
    model('planning_intelligence', 'ScheduleResource').objects.filter(pk=references['resource']).update(employee_id=references['employee'])
    model('project_control', 'ApprovedHourEntry').objects.filter(pk=references['hour']).update(employee_id=references['employee'])
    model('project_organizer', 'Project').objects.filter(pk=references['organizer']).update(enterprise_project_id=references['project'])
    model('invoice_tracker', 'CustomerInvoice').objects.filter(pk=references['invoice']).update(
        canonical_project_id=references['project'], canonical_client_id=references['client'], canonical_identity_basis={'source': 'synthetic review'})
    model('finance', 'ReceivablesSourceIdentity').objects.create(source_row_id=references['source'],
        canonical_project_id=references['project'], canonical_client_id=references['client'], canonical_identity_basis={'source': 'synthetic review'})
    request_id = uuid4()
    command = {'actor_id': references['actor'], 'request_id': request_id, 'source_type': 'project_client',
               'source_id': str(references['project']), 'request_hash': 'b' * 64, 'reason': 'Synthetic migration verification'}
    model('core', 'SharedRecordLinkCommand').objects.create(**command)
    try:
        with transaction.atomic():
            model('core', 'SharedRecordLinkCommand').objects.create(**command)
    except IntegrityError:
        pass
    else:
        raise AssertionError('Duplicate request identities were accepted.')
    try:
        with transaction.atomic():
            model('planning_intelligence', 'ScheduleResource').objects.filter(pk=references['resource']).update(employee_id=uuid4())
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    except IntegrityError:
        pass
    else:
        raise AssertionError('A nonexistent canonical employee was accepted.')
    print('PASS: actual PostgreSQL writes reject duplicate retry keys and nonexistent canonical employees.')
    for migration in reversed(migrations):
        try:
            with connection.schema_editor() as editor:
                migration.unapply(before[(migration.app_label, migration.name)].clone(), editor)
        except RuntimeError as exc:
            if not any(word in str(exc).lower() for word in ('preserve', 'reviewed', 'retaining', 'evidence')):
                raise
            print(f'PASS: rollback guard retained {migration.app_label}.{migration.name}')
        else:
            raise AssertionError(f'Missing rollback guard: {migration.app_label}.{migration.name}')
    assert source_fingerprints(state.apps, fields) == original, 'Linking or rollback changed original source facts.'
    verify_constraints(connection, state.apps)
    assert model('core', 'SharedRecordLinkCommand').objects.count() == 1
    assert model('finance', 'ReceivablesSourceIdentity').objects.count() == 1
    print(f'PASS: final database {database} retains all six new schemas, reviewed synthetic references, and original source facts.')
    print('BOUNDARY: isolated additive-operation verification; historical all-app migration chain and runtime data are not certified.')


if __name__ == '__main__':
    main()
