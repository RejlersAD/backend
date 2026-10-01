"""Verify order-2 additive migration operations on dedicated disposable PostgreSQL.

This reconstructs the preceding schema from synchronized current models. It does
not certify a fresh replay of the repository's entire historical migration chain.
Only a loopback, nondefault port with explicit synthetic credentials is accepted;
the checker refuses a populated database and never drops one.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4


MIGRATION = '0015_bid_preparation'
SOURCE_MODELS = (
    ('users', 'User'), ('sales', 'Client'), ('sales', 'Deal'), ('sales', 'Quote'),
    ('planning_intelligence', 'PlanningProject'), ('planning_intelligence', 'PlanningGeneration'),
    ('planning_intelligence', 'Schedule'), ('planning_intelligence', 'ScheduleVersion'),
    ('planning_intelligence', 'TechnicalProposal'), ('planning_intelligence', 'ScheduleResource'),
)


def provision_database():
    import psycopg2
    from psycopg2 import sql

    port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
    password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
    database = os.environ.get('RADAI_BID_MIGRATION_DATABASE', 'bid_preparation_migration_verify')
    if not re.fullmatch(r'bid_preparation_migration_verify(?:_[a-z0-9]+)?', database):
        raise RuntimeError('Use a dedicated bid-preparation synthetic database name.')
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


def seed_legacy(registry):
    model = registry.get_model
    actor = model('users', 'User').objects.create(username='bid-migration-user', email='bid-migration@example.test')
    client = model('sales', 'Client').objects.create(client_code='BID-MIG-CLI',
        company_name='Original synthetic client', account_manager_id=actor.pk)
    deal = model('sales', 'Deal').objects.create(deal_code='BID-MIG-OPP',
        deal_name='Original synthetic bid', client_id=client.pk, owner_id=actor.pk,
        stage='proposal', bid_decision='bid', currency='AED', estimated_value=Decimal('43210.98'),
        description='Original scope', submission_due_date=date(2026, 11, 1))
    quote = model('sales', 'Quote').objects.create(quote_number='BID-MIG-QUOTE',
        deal_id=deal.pk, client_id=client.pk, prepared_by_id=actor.pk, scope='Retained authored scope',
        subtotal=Decimal('12345.67'), tax_amount=Decimal('617.28'), total_amount=Decimal('12962.95'),
        estimated_cost=Decimal('8765.43'), currency='AED', valid_until=date(2026, 12, 1),
        status='submitted', submitted_version_hash='c' * 64,
        approval_history=[{'decision': 'approved', 'source': 'synthetic preserved history'}],
        deliverables=['Retained deliverable'], estimated_hours={'process': '12.50'})
    project = model('planning_intelligence', 'PlanningProject').objects.create(
        name='Original planning workspace', client='Original client label', duration_months=Decimal('3'),
        created_by_id=actor.pk, scope_summary='Original planning scope')
    generation = model('planning_intelligence', 'PlanningGeneration').objects.create(
        project_id=project.pk, version=1, generated_by_id=actor.pk,
        manhours={'total_hours': '12.50'}, eddr=[{'document_number': 'D-1', 'title': 'Original deliverable'}])
    schedule = model('planning_intelligence', 'Schedule').objects.create(
        project_id=project.pk, code='BID-MIG-SCHED', name='Original schedule', planned_start=date(2026, 11, 1))
    version = model('planning_intelligence', 'ScheduleVersion').objects.create(
        schedule_id=schedule.pk, version=1, source_generation_id=generation.pk, status='approved')
    technical = model('planning_intelligence', 'TechnicalProposal').objects.create(
        project_id=project.pk, schedule_version_id=version.pk, source_generation_id=generation.pk,
        proposal_number='BID-MIG-TECH', title='Original technical proposal', revision=1,
        status='approved', created_by_id=actor.pk, approved_by_id=actor.pk,
        snapshot={'generation': {'id': generation.pk, 'version': 1}},
        sections=[{'key': 'scope', 'content': 'Retained technical scope', 'included': True}])
    model('planning_intelligence', 'ScheduleResource').objects.create(
        project_id=project.pk, code='BID-MIG-ROLE', name='Original role', unit_cost=Decimal('23.45'))
    return {'actor': actor.pk, 'deal': deal.pk, 'quote': quote.pk,
            'project': project.pk, 'technical': technical.pk}


def fingerprints(registry, fields):
    from django.core.serializers.json import DjangoJSONEncoder
    return {
        key: hashlib.sha256(json.dumps(list(registry.get_model(*key).objects.order_by('pk').values(*names)),
            sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()
        for key, names in fields.items()
    }


def verify_constraints(connection, registry, model_names):
    from django.db.models import UniqueConstraint
    total_foreign_keys = 0
    for name in model_names:
        model = registry.get_model('sales', name)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        for field in model._meta.local_fields:
            if field.remote_field and getattr(field, 'db_constraint', False):
                assert any(value['foreign_key'] and value['columns'] == [field.column]
                    for value in constraints.values()), (name, field.name, 'missing FK')
                total_foreign_keys += 1
            if field.unique:
                assert any(value['unique'] and value['columns'] == [field.column]
                    for value in constraints.values()), (name, field.name, 'missing unique')
        for declared in model._meta.constraints:
            if isinstance(declared, UniqueConstraint):
                assert constraints.get(declared.name, {}).get('unique'), declared.name
    print('PASS: database foreign keys and declared uniqueness', total_foreign_keys, flush=True)


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
    from django.db.migrations import CreateModel, AddConstraint, RunPython
    from django.db.migrations.state import ProjectState

    assert connection.vendor == 'postgresql' and connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    with connection.cursor() as cursor:
        if connection.introspection.table_names(cursor):
            raise RuntimeError('Refusing populated database; choose a fresh synthetic name.')
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    migration = importlib.import_module(f'apps.sales.migrations.{MIGRATION}').Migration(MIGRATION, 'sales')
    assert all(isinstance(operation, (CreateModel, AddConstraint, RunPython)) for operation in migration.operations), (
        'Review checker for migration operations beyond new models/constraints/guard.')
    model_names = [operation.name for operation in migration.operations if isinstance(operation, CreateModel)]
    baseline = ProjectState.from_apps(apps)
    for name in model_names:
        baseline.remove_model('sales', name.lower())
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    print('PASS: real empty reverse operations', flush=True)
    refs = seed_legacy(baseline.apps)
    fields = {key: [field.attname for field in baseline.apps.get_model(*key)._meta.local_fields]
              for key in SOURCE_MODELS}
    original = fingerprints(baseline.apps, fields)
    with connection.schema_editor() as editor:
        state = migration.apply(baseline.clone(), editor)
    assert fingerprints(state.apps, fields) == original
    for name in model_names:
        assert state.apps.get_model('sales', name).objects.count() == 0
    print('PASS: real forward migration; ten source model field hashes unchanged; no backfill', flush=True)
    verify_constraints(connection, state.apps, model_names)
    model = state.apps.get_model
    preparation = model('sales', 'BidPreparation').objects.create(
        opportunity_id=refs['deal'], planning_project_id=refs['project'], created_by_id=refs['actor'],
        reason='Synthetic migration review', source_basis={'duration_source': 'opportunity'})
    capture = model('sales', 'QuotePreparationRevision').objects.create(
        quote_id=refs['quote'], preparation_id=preparation.pk, technical_proposal_id=refs['technical'],
        revision=1, source={'technical_id': refs['technical']}, evidence={'source': 'synthetic'},
        source_fingerprint='a' * 64, selected_fields=['scope'], before_fields={'scope': 'Retained authored scope'},
        applied_fields={'scope': 'Retained technical scope'}, reason='Synthetic reviewed capture',
        created_by_id=refs['actor'])
    command_values = {'actor_id': refs['actor'], 'request_id': uuid4(), 'action': 'capture',
                      'request_hash': 'b' * 64, 'preparation_id': preparation.pk, 'capture_id': capture.pk}
    model('sales', 'BidPreparationCommand').objects.create(**command_values)
    for mutation in (
        lambda: model('sales', 'BidPreparationCommand').objects.create(**{**command_values, 'capture_id': None}),
        lambda: model('sales', 'QuotePreparationRevision').objects.create(
            quote_id=refs['quote'], preparation_id=preparation.pk, technical_proposal_id=-987654321,
            revision=2, source_fingerprint='d' * 64, reason='Invalid synthetic foreign key'),
    ):
        try:
            with transaction.atomic():
                mutation()
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        except IntegrityError:
            pass
        else:
            raise AssertionError('An invalid retry key or nonexistent source was accepted.')
    print('PASS: PostgreSQL rejected duplicate retry identity and nonexistent technical source', flush=True)
    try:
        with connection.schema_editor() as editor:
            migration.unapply(baseline.clone(), editor)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Rollback guard did not preserve preparation evidence.')
    assert model('sales', 'BidPreparationCommand').objects.count() == 1
    assert model('sales', 'QuotePreparationRevision').objects.count() == 1
    assert fingerprints(state.apps, fields) == original
    verify_constraints(connection, state.apps, model_names)
    print('PASS: guarded reverse preserved connection/capture/command and original facts', flush=True)
    print('BOUNDARY: actual additive operations; no full historical-chain replay or production changes', flush=True)


if __name__ == '__main__':
    main()
