"""Exercise actual additive attachment DDL on synthetic loopback PostgreSQL.

Current models reconstruct the preceding schema; this does not certify a full
historical migration-chain replay. No application database, file storage or
external service is accessed. A populated verification database is never erased.
"""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from uuid import uuid4


MIGRATION = '0017_attachment_storage_encoding'
NEW_FIELDS = {'storage_encoding', 'stored_size', 'stored_sha256'}
SOURCE_MODELS = (
    ('users', 'User'), ('sales', 'Client'), ('sales', 'Deal'), ('sales', 'Quote'),
    ('sales', 'OpportunityWorkspace'), ('sales', 'OpportunityWorkspaceUpload'),
    ('sales', 'OpportunityAuditEvent'), ('sales', 'OpportunityFolderTag'),
    ('sales', 'ProposalReviewDocument'), ('sales', 'ProposalReviewComment'),
    ('sales', 'ProposalReviewCommand'),
)


def provision_database():
    import psycopg2
    from psycopg2 import sql

    port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
    password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
    database = os.environ.get('RADAI_ATTACHMENT_MIGRATION_DATABASE', 'upload_compression_migration_verify')
    if not re.fullmatch(r'upload_compression_migration_verify(?:_[a-z0-9]+)?', database):
        raise RuntimeError('Use a dedicated synthetic attachment verification database name.')
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
    actor = model('users', 'User').objects.create(username='compression-migration',
        email='compression-migration@example.test')
    client = model('sales', 'Client').objects.create(client_code='COMP-MIG-CLIENT',
        company_name='Retained synthetic client', account_manager_id=actor.pk)
    deal = model('sales', 'Deal').objects.create(deal_code='COMP-MIG-VF',
        deal_name='Retained opportunity', client_id=client.pk, owner_id=actor.pk,
        stage='proposal', bid_decision='bid', currency='AED', estimated_value='12345.67',
        description='Retained original scope', submission_due_date=date(2026, 11, 1))
    workspace = model('sales', 'OpportunityWorkspace').objects.create(opportunity_id=deal.pk,
        requested_by_id=actor.pk, status='failed', config_fingerprint='c' * 64,
        root_item_id='synthetic-retained-root', folders={'tender': {'id': 'synthetic-folder'}},
        intent={'operation': 'retained remote recovery'}, error_code='recovery_required')
    model('sales', 'OpportunityFolderTag').objects.create(opportunity_id=deal.pk,
        folder_key='proposal', tag='Retained custom tag', revision=4,
        last_request_hash='d' * 64, updated_by_id=actor.pk)
    model('sales', 'OpportunityWorkspaceUpload').objects.create(workspace_id=workspace.pk,
        request_id=uuid4(), actor_id=actor.pk, folder_key='tender', name='Retained remote.pdf',
        size=321, sha256='a' * 64, provider='sharepoint', status='ready',
        result={'id': 'synthetic-existing-file', 'name': 'Retained remote.pdf', 'version': '3.0'})
    private_id = uuid4()
    private = model('sales', 'OpportunityWorkspaceUpload').objects.create(id=private_id,
        workspace_id=workspace.pk, request_id=uuid4(), actor_id=actor.pk,
        folder_key='proposal', name='Retained proposal.pdf', size=654, sha256='b' * 64,
        provider='radai', status='ready',
        storage_name=f'sales-opportunity-attachments/{deal.pk}/{private_id}/original',
        storage_fingerprint='e' * 64, normalized_name='f' * 64, mime_type='application/pdf')
    model('sales', 'OpportunityWorkspaceUpload').objects.create(workspace_id=workspace.pk,
        request_id=uuid4(), actor_id=actor.pk, folder_key='award', name='Retained uncertain.pdf',
        size=123, sha256='1' * 64, provider='radai', status='uncertain',
        storage_fingerprint='e' * 64, error_code='private_storage_unavailable')
    quote = model('sales', 'Quote').objects.create(quote_number='COMP-MIG-QUOTE',
        deal_id=deal.pk, client_id=client.pk, prepared_by_id=actor.pk,
        subtotal='1234.56', total_amount='1234.56', valid_until=date(2026, 12, 1),
        scope='Retained commercial scope', status='submitted', submitted_version_hash='2' * 64,
        approval_history=[{'source': 'synthetic preserved approval'}])
    document = model('sales', 'ProposalReviewDocument').objects.create(quote_id=quote.pk,
        attachment_id=private.pk, revision=2, name=private.name, sha256=private.sha256,
        size=private.size, page_count=3, feedback_version=5, created_by_id=actor.pk)
    model('sales', 'ProposalReviewComment').objects.create(document_id=document.pk,
        body='Retained review evidence', author_id=actor.pk, page_number=2)
    model('sales', 'ProposalReviewCommand').objects.create(quote_id=quote.pk,
        document_id=document.pk, actor_id=actor.pk, request_id=uuid4(), action='comment',
        payload_hash='3' * 64, result={'document_id': str(document.pk), 'feedback_version': 5})
    model('sales', 'OpportunityAuditEvent').objects.create(opportunity_id=deal.pk,
        event_type='proposal_review_comment', actor_id=actor.pk,
        reason='Retained synthetic audit', data={'document_id': str(document.pk)})
    return private.pk


def fingerprints(registry, fields):
    from django.core.serializers.json import DjangoJSONEncoder

    return {key: hashlib.sha256(json.dumps(
        list(registry.get_model(*key).objects.order_by('pk').values(*names)),
        sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()
        for key, names in fields.items()}


def verify_constraints(connection, registry):
    from django.db.models import UniqueConstraint

    foreign_keys = 0
    for key in SOURCE_MODELS:
        model = registry.get_model(*key)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        for field in model._meta.local_fields:
            if field.remote_field and getattr(field, 'db_constraint', False):
                assert any(value['foreign_key'] and value['columns'] == [field.column]
                           for value in constraints.values()), (key, field.name, 'missing database FK')
                foreign_keys += 1
        for constraint in model._meta.constraints:
            if isinstance(constraint, UniqueConstraint):
                assert constraints.get(constraint.name, {}).get('unique'), constraint.name
        if key == ('sales', 'OpportunityWorkspaceUpload'):
            assert constraints['sales_upload_storage_encoding']['check']
    print(f'PASS: retained {foreign_keys} database FKs and declared uniqueness; actual encoding constraint', flush=True)


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
    from django.db.migrations import AddField, AddConstraint, RunPython
    from django.db.migrations.state import ProjectState

    assert connection.vendor == 'postgresql' and connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    with connection.cursor() as cursor:
        if connection.introspection.table_names(cursor):
            raise RuntimeError('Refusing populated database; choose a fresh dedicated synthetic name.')
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    migration = importlib.import_module(f'apps.sales.migrations.{MIGRATION}').Migration(MIGRATION, 'sales')
    assert all(isinstance(operation, (AddField, AddConstraint, RunPython)) for operation in migration.operations)
    assert {operation.name for operation in migration.operations if isinstance(operation, AddField)} == NEW_FIELDS
    baseline = ProjectState.from_apps(apps)
    upload_state = baseline.models['sales', 'opportunityworkspaceupload']
    for name in NEW_FIELDS:
        upload_state.fields.pop(name)
    upload_state.options['constraints'] = [constraint for constraint in upload_state.options['constraints']
                                          if constraint.name != 'sales_upload_storage_encoding']
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    print('PASS: actual empty reverse reconstructed preceding attachment schema', flush=True)
    private_id = seed_legacy(baseline.apps)
    fields = {key: [field.attname for field in baseline.apps.get_model(*key)._meta.local_fields]
              for key in SOURCE_MODELS}
    original = fingerprints(baseline.apps, fields)
    with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No Graph during DDL')), \
            patch('apps.sales.attachment_storage.attachment_storage', side_effect=AssertionError('No file storage during DDL')):
        with connection.schema_editor() as editor:
            state = migration.apply(baseline.clone(), editor)
    uploads = state.apps.get_model('sales', 'OpportunityWorkspaceUpload').objects
    assert uploads.count() == 3
    assert not uploads.exclude(storage_encoding='identity', stored_size__isnull=True, stored_sha256='').exists()
    assert fingerprints(state.apps, fields) == original
    print('PASS: real forward DDL preserved all original fields across eleven models; legacy defaults and no object backfill', flush=True)
    verify_constraints(connection, state.apps)
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    assert fingerprints(baseline.apps, fields) == original
    with connection.schema_editor() as editor:
        state = migration.apply(baseline.clone(), editor)
    uploads = state.apps.get_model('sales', 'OpportunityWorkspaceUpload').objects
    assert fingerprints(state.apps, fields) == original
    print('PASS: legacy-only reverse/reapply retained remote/private/uncertain uploads and protected proposal review evidence', flush=True)

    for label, values in (
        ('unsupported encoding', {'storage_encoding': 'brotli'}),
        ('negative encoded size', {'stored_size': -1}),
        ('missing workspace FK', {'workspace_id': uuid4()}),
        ('missing actor FK', {'actor_id': -987654321}),
    ):
        try:
            with transaction.atomic():
                uploads.filter(pk=private_id).update(**values)
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        except IntegrityError:
            pass
        else:
            raise AssertionError(f'PostgreSQL accepted {label}.')
    values = uploads.get(pk=private_id)
    for label, extra in (
        ('duplicate request identity', {'request_id': values.request_id, 'normalized_name': None}),
        ('duplicate private filename', {'request_id': uuid4(), 'normalized_name': values.normalized_name}),
    ):
        try:
            with transaction.atomic():
                uploads.create(workspace_id=values.workspace_id, actor_id=values.actor_id,
                    folder_key=values.folder_key, name=values.name, size=values.size,
                    sha256=values.sha256, provider='radai', **extra)
        except IntegrityError:
            pass
        else:
            raise AssertionError(f'PostgreSQL accepted {label}.')
    assert fingerprints(state.apps, fields) == original
    print('PASS: PostgreSQL rejected invalid encoding/size/FKs and retained upload retry/filename uniqueness', flush=True)

    encoded = gzip.compress(b'synthetic immutable source evidence', mtime=0)
    for representation in (
        {'storage_encoding': 'gzip', 'stored_size': len(encoded), 'stored_sha256': hashlib.sha256(encoded).hexdigest()},
        {'storage_encoding': 'identity', 'stored_size': values.size, 'stored_sha256': values.sha256},
        {'storage_encoding': 'identity', 'stored_size': None, 'stored_sha256': values.sha256},
    ):
        uploads.filter(pk=private_id).update(**representation)
        try:
            with connection.schema_editor() as editor:
                migration.unapply(baseline.clone(), editor)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Reverse migration discarded stored-representation evidence.')
        assert fingerprints(state.apps, fields) == original
        assert uploads.filter(pk=private_id, **representation).exists()
    verify_constraints(connection, state.apps)
    print('PASS: rollback guards preserved gzip/raw/partial representation metadata and every original source fact', flush=True)
    print('BOUNDARY: actual additive DDL, synthetic PostgreSQL; no full historic-chain replay or application/storage writes', flush=True)


if __name__ == '__main__':
    main()
