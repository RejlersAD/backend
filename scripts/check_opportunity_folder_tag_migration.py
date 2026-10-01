"""Verify actual folder-tag DDL against synthetic disposable PostgreSQL.

Current-model synchronization reconstructs the preceding schema, then this probe
executes the actual new migration forward and backward. It does not certify a
fresh replay of the repository's full historical migration chain. It never uses
the running application's credentials, writes files, calls Graph or drops a DB.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from uuid import uuid4


MIGRATION = '0016_opportunity_folder_tags'
MODEL = 'OpportunityFolderTag'
SOURCE_MODELS = (
    ('users', 'User'), ('sales', 'Client'), ('sales', 'Deal'),
    ('sales', 'OpportunityWorkspace'), ('sales', 'OpportunityWorkspaceUpload'),
    ('sales', 'OpportunityAuditEvent'),
)


def provision_database():
    import psycopg2
    from psycopg2 import sql

    port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
    password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
    database = os.environ.get('RADAI_FOLDER_TAG_MIGRATION_DATABASE', 'folder_tags_migration_verify')
    if not re.fullmatch(r'folder_tags_migration_verify(?:_[a-z0-9]+)?', database):
        raise RuntimeError('Use a dedicated synthetic folder-tag verification database name.')
    if not port.isdecimal() or not 1024 <= int(port) <= 65535 or int(port) == 5432 or not password:
        raise RuntimeError('Explicit disposable PostgreSQL port and synthetic password are required.')
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
    actor = model('users', 'User').objects.create(username='folder-tag-migration',
                                                 email='folder-tag-migration@example.test')
    client = model('sales', 'Client').objects.create(client_code='TAG-MIG-CLIENT',
        company_name='Retained synthetic client', account_manager_id=actor.pk)
    deal = model('sales', 'Deal').objects.create(deal_code='TAG-MIG-VF1', deal_name='Retained opportunity',
        client_id=client.pk, owner_id=actor.pk, stage='proposal', bid_decision='bid',
        currency='AED', estimated_value=Decimal('12345.67'), weighted_value=Decimal('6172.84'),
        description='Retained original scope', submission_due_date=date(2026, 11, 1),
        bid_decision_reason='Retained decision', bid_decided_by_id=actor.pk)
    unprovisioned = model('sales', 'Deal').objects.create(deal_code='TAG-MIG-VF2',
        deal_name='No storage setup requested', client_id=client.pk, owner_id=actor.pk)
    workspace = model('sales', 'OpportunityWorkspace').objects.create(opportunity_id=deal.pk,
        requested_by_id=actor.pk, status='failed', config_fingerprint='c' * 64,
        root_item_id='synthetic-retained-root', web_url='https://synthetic.sharepoint.com/sites/Synthetic/VF1',
        folders={'tender': {'id': 'synthetic-retained-folder'}},
        intent={'operation': 'existing remote recovery', 'folder_key': 'proposal'},
        error_code='recovery_required', lease_token=uuid4(),
        lease_until=datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    model('sales', 'OpportunityWorkspaceUpload').objects.create(workspace_id=workspace.pk,
        request_id=uuid4(), actor_id=actor.pk, folder_key='tender', name='retained-source.pdf',
        size=321, sha256='a' * 64, provider='sharepoint', status='ready',
        result={'id': 'synthetic-existing-file', 'name': 'retained-source.pdf', 'version': '3.0'})
    upload_id = uuid4()
    model('sales', 'OpportunityWorkspaceUpload').objects.create(id=upload_id,
        workspace_id=workspace.pk, request_id=uuid4(), actor_id=actor.pk,
        folder_key='proposal', name='retained-private.pdf', size=654, sha256='b' * 64,
        provider='radai', status='uncertain', error_code='private_storage_unavailable',
        storage_name=f'sales-opportunity-attachments/{deal.pk}/{upload_id}/original',
        storage_fingerprint='d' * 64, normalized_name='e' * 64, mime_type='application/pdf')
    model('sales', 'OpportunityAuditEvent').objects.create(opportunity_id=deal.pk,
        event_type='workspace_upload_started', actor_id=actor.pk,
        reason='Retained synthetic audit', data={'upload_id': str(upload_id), 'folder_key': 'proposal'})
    return {'actor': actor.pk, 'deal': deal.pk, 'unprovisioned': unprovisioned.pk}


def fingerprints(registry, fields):
    from django.core.serializers.json import DjangoJSONEncoder

    return {key: hashlib.sha256(json.dumps(
        list(registry.get_model(*key).objects.order_by('pk').values(*names)),
        sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()
        for key, names in fields.items()}


def verify_constraints(connection, registry):
    model = registry.get_model('sales', MODEL)
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
    foreign_keys = 0
    for field in model._meta.local_fields:
        if field.remote_field and getattr(field, 'db_constraint', False):
            assert any(value['foreign_key'] and value['columns'] == [field.column]
                       for value in constraints.values()), (field.name, 'missing database FK')
            foreign_keys += 1
    assert foreign_keys == 2, 'Opportunity and actor FK constraints are required.'
    assert constraints['sales_opportunity_folder_tag_uq']['unique']
    assert constraints['sales_opportunity_folder_tag_key']['check']
    print('PASS: real PostgreSQL opportunity/actor FKs, folder uniqueness and six-category check', flush=True)


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
    from django.db import connection, DataError, IntegrityError, transaction
    from django.db.migrations import CreateModel, AddConstraint, RunPython
    from django.db.migrations.state import ProjectState

    assert connection.vendor == 'postgresql' and connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    with connection.cursor() as cursor:
        if connection.introspection.table_names(cursor):
            raise RuntimeError('Refusing populated database; choose a fresh dedicated synthetic name.')
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    migration = importlib.import_module(f'apps.sales.migrations.{MIGRATION}').Migration(MIGRATION, 'sales')
    assert all(isinstance(operation, (CreateModel, AddConstraint, RunPython)) for operation in migration.operations)
    assert [operation.name for operation in migration.operations if isinstance(operation, CreateModel)] == [MODEL]
    baseline = ProjectState.from_apps(apps)
    baseline.remove_model('sales', MODEL.lower())
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    print('PASS: real empty reverse operations reconstructed preceding schema', flush=True)
    refs = seed_legacy(baseline.apps)
    fields = {key: [field.attname for field in baseline.apps.get_model(*key)._meta.local_fields]
              for key in SOURCE_MODELS}
    original = fingerprints(baseline.apps, fields)
    with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No Graph calls during migration')):
        with connection.schema_editor() as editor:
            state = migration.apply(baseline.clone(), editor)
    tag_model = state.apps.get_model('sales', MODEL)
    assert tag_model.objects.count() == 0
    assert fingerprints(state.apps, fields) == original
    assert not state.apps.get_model('sales', 'OpportunityWorkspace').objects.filter(opportunity_id=refs['unprovisioned']).exists()
    print('PASS: real forward migration preserved every field across six source models; no tags/backfill/setup', flush=True)
    verify_constraints(connection, state.apps)
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    assert fingerprints(baseline.apps, fields) == original
    with connection.schema_editor() as editor:
        state = migration.apply(baseline.clone(), editor)
    tag_model = state.apps.get_model('sales', MODEL)
    assert tag_model.objects.count() == 0
    print('PASS: empty reverse/reapply with existing Deal/workspace/upload/audit facts retained', flush=True)

    blank = tag_model.objects.create(opportunity_id=refs['unprovisioned'], folder_key='tender',
                                    last_request_hash='f' * 64, updated_by_id=refs['actor'])
    assert blank.tag == '' and blank.revision == 1
    assert not state.apps.get_model('sales', 'OpportunityWorkspace').objects.filter(opportunity_id=refs['unprovisioned']).exists()
    values = {'opportunity_id': refs['deal'], 'folder_key': 'proposal', 'tag': 'Custom review',
              'last_request_hash': 'g' * 64, 'updated_by_id': refs['actor']}
    tag = tag_model.objects.create(**values)
    invalid = [
        ('duplicate opportunity/category', values),
        ('unknown category', {**values, 'folder_key': 'arbitrary'}),
        ('missing opportunity', {**values, 'opportunity_id': uuid4()}),
        ('missing actor', {**values, 'folder_key': 'award', 'updated_by_id': uuid4()}),
        ('overlong tag', {**values, 'folder_key': 'internal', 'tag': 'x' * 65}),
    ]
    for label, invalid_values in invalid:
        try:
            with transaction.atomic():
                tag_model.objects.create(**invalid_values)
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        except (IntegrityError, DataError):
            pass
        else:
            raise AssertionError(f'PostgreSQL accepted {label}.')
    print('PASS: PostgreSQL rejected duplicate/category/foreign-key/length violations; empty default is safe', flush=True)

    # Clearing a custom value retains its revision/actor evidence. Reversal must
    # refuse both nonempty and cleared rows, not silently remove that history.
    for cleared in (False, True):
        if cleared:
            tag_model.objects.filter(pk=tag.pk).update(tag='', revision=2, last_request_hash='h' * 64)
        try:
            with connection.schema_editor() as editor:
                migration.unapply(baseline.clone(), editor)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Reverse migration discarded retained folder-tag evidence.')
        assert tag_model.objects.count() == 2
        assert fingerprints(state.apps, fields) == original
    tag.refresh_from_db()
    assert tag.tag == '' and tag.revision == 2 and tag.updated_by_id == refs['actor']
    verify_constraints(connection, state.apps)
    print('PASS: reverse guards retained custom and cleared revisions plus all original storage facts', flush=True)
    print('BOUNDARY: actual additive DDL, synthetic loopback PostgreSQL; no full historic-chain replay or production changes', flush=True)


if __name__ == '__main__':
    main()
