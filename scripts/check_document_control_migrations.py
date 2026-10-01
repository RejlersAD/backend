"""Verify real document-control DDL on a fresh disposable PostgreSQL database.

Reconstructs Sales 0017 from current model state, then exercises 0018/0019.
This is not a replay of the repository's entire historical migration chain.
No client files, provider calls or application database credentials are used.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from uuid import uuid4

from check_attachment_storage_encoding_migration import SOURCE_MODELS, fingerprints, seed_legacy


def provision_database():
    import psycopg2
    from psycopg2 import sql

    port = os.environ.get('RADAI_CONCURRENCY_PG_PORT', '')
    password = os.environ.get('RADAI_CONCURRENCY_PG_PASSWORD', '')
    database = os.environ.get('RADAI_DOCUMENT_MIGRATION_DATABASE', 'document_control_migration_verify')
    if not re.fullmatch(r'document_control_migration_verify(?:_[a-z0-9]+)?', database):
        raise RuntimeError('Use a dedicated synthetic document-control database name.')
    if not port.isdecimal() or not 1024 <= int(port) <= 65535 or int(port) == 5432 or not password:
        raise RuntimeError('Explicit disposable PostgreSQL port and synthetic password are required.')
    admin = psycopg2.connect(host='127.0.0.1', port=port, dbname='postgres',
                            user='radai_pr_concurrency', password=password, connect_timeout=5)
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_database WHERE datname=%s', [database])
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    finally:
        admin.close()
    return database


def preceding_state(current, migrations):
    from django.db import migrations as operations, models

    state = current.clone()
    # Only additive model/field/constraint operations are expected. Restore the
    # one replaced name constraint to its exact pre-versioning scope.
    for migration in reversed(migrations):
        for operation in reversed(migration.operations):
            if isinstance(operation, operations.RunPython):
                continue
            if isinstance(operation, operations.CreateModel):
                state.remove_model('sales', operation.name.lower())
            elif isinstance(operation, operations.AddField):
                state.remove_field('sales', operation.model_name.lower(), operation.name)
            elif isinstance(operation, operations.AddConstraint):
                state.remove_constraint('sales', operation.model_name.lower(), operation.constraint.name)
            elif isinstance(operation, operations.RemoveConstraint) and operation.name == 'sales_private_attachment_name':
                state.add_constraint('sales', operation.model_name.lower(), models.UniqueConstraint(
                    fields=('workspace', 'folder_key', 'normalized_name'),
                    condition=models.Q(provider='radai'), name='sales_private_attachment_name'))
            else:
                raise RuntimeError(f'Unexpected migration operation: {type(operation).__name__}')
    return state


def assert_rejected(connection, operation, label):
    from django.db import DataError, IntegrityError, transaction

    try:
        with transaction.atomic():
            operation()
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    except (DataError, IntegrityError):
        return
    raise AssertionError(f'PostgreSQL accepted {label}')


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
    from django.db import connection
    from django.db.migrations.state import ProjectState

    assert connection.vendor == 'postgresql' and connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    if connection.introspection.table_names():
        raise RuntimeError('Refusing a populated verification database; use a new synthetic name.')
    names = ['0018_document_versions']
    classification = sorted(Path(__file__).resolve().parents[1].joinpath('apps/sales/migrations').glob('0019*.py'))
    if len(classification) != 1:
        raise RuntimeError('Expected exactly one classification migration after document versions.')
    names.append(classification[0].stem)
    migrations = [importlib.import_module(f'apps.sales.migrations.{name}').Migration(name, 'sales') for name in names]
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    current = ProjectState.from_apps(apps)
    baseline = preceding_state(current, migrations)
    states = [baseline]
    for migration in migrations:
        after = states[-1].clone()
        for operation in migration.operations:
            operation.state_forwards('sales', after)
        states.append(after)
    for index in reversed(range(len(migrations))):
        with connection.schema_editor() as editor:
            migrations[index].unapply(states[index].clone(), editor)
    print('PASS: real empty reverse reconstructed the preceding attachment schema', flush=True)

    original_upload_id = seed_legacy(baseline.apps)
    fields = {key: [field.attname for field in baseline.apps.get_model(*key)._meta.local_fields] for key in SOURCE_MODELS}
    original = fingerprints(baseline.apps, fields)
    with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No Graph during DDL')), \
            patch('apps.sales.attachment_storage.attachment_storage', side_effect=AssertionError('No storage during DDL')):
        for index, migration in enumerate(migrations):
            with connection.schema_editor() as editor:
                migration.apply(states[index].clone(), editor)
    assert fingerprints(states[-1].apps, fields) == original
    for key in set(current.models) - set(baseline.models):
        assert states[-1].apps.get_model(*key).objects.count() == 0, key
    print('PASS: forward DDL preserved original fields across eleven source models; no document/job backfill', flush=True)

    for index in reversed(range(len(migrations))):
        with connection.schema_editor() as editor:
            migrations[index].unapply(states[index].clone(), editor)
    assert fingerprints(baseline.apps, fields) == original
    for index, migration in enumerate(migrations):
        with connection.schema_editor() as editor:
            migration.apply(states[index].clone(), editor)
    assert fingerprints(states[-1].apps, fields) == original
    print('PASS: legacy-only reverse/reapply retained uploads, folder tags and protected proposal evidence', flush=True)

    model = states[-1].apps.get_model
    uploads = model('sales', 'OpportunityWorkspaceUpload').objects
    source = uploads.get(pk=original_upload_id)
    document = model('sales', 'OpportunityDocument').objects.create(
        id=source.pk, workspace_id=source.workspace_id, folder_key=source.folder_key,
        name=source.name, normalized_name=source.normalized_name,
        root_upload_id=source.pk, head_upload_id=source.pk)
    uploads.filter(pk=source.pk).update(document_id=document.pk)
    second_values = dict(workspace_id=source.workspace_id, actor_id=source.actor_id,
                         folder_key=source.folder_key, name=source.name, size=777, sha256='9' * 64,
                         provider='radai', status='ready', document_id=document.pk, version_number=2,
                         previous_upload_id=source.pk, normalized_name=source.normalized_name,
                         revision_note='Synthetic second revision', expected_head_token='8' * 64)
    second = uploads.create(request_id=uuid4(), **second_values)
    assert_rejected(connection, lambda: uploads.create(request_id=uuid4(), **second_values), 'duplicate document revision')
    assert_rejected(connection, lambda: uploads.filter(pk=second.pk).update(version_number=0), 'zero revision number')
    assert_rejected(connection, lambda: uploads.filter(pk=second.pk).update(previous_upload_id=uuid4()), 'missing historical source FK')
    assert model('sales', 'ProposalReviewDocument').objects.get(attachment_id=source.pk).sha256 == source.sha256
    print('PASS: version uniqueness/positive/FK constraints; original proposal reference remains unchanged', flush=True)

    classification_row = model('sales', 'OpportunityDocumentClassification').objects.create(
        document_id=document.pk, confirmed_type='technical_proposal', revision=1, updated_by_id=source.actor_id)
    assert classification_row.revision == 1
    for index in (1, 0):
        try:
            with connection.schema_editor() as editor:
                migrations[index].unapply(states[index].clone(), editor)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f'Reverse of {names[index]} discarded populated evidence.')
    assert uploads.filter(document_id=document.pk).count() == 2
    assert model('sales', 'OpportunityDocumentClassification').objects.get(pk=classification_row.pk).confirmed_type == 'technical_proposal'
    print('PASS: populated reverse guards retain document versions and reviewed classifications', flush=True)
    print('BOUNDARY: actual additive PostgreSQL DDL on synthetic data; no complete historical-chain or production claim', flush=True)


if __name__ == '__main__':
    main()
