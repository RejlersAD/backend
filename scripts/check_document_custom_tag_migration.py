"""Exercise only Sales 0020 DDL against retained synthetic document-control data.

Current models reconstruct the preceding 0019 schema. This is an additive DDL
probe, not a replay of 0018/0019 or the complete historical migration chain.
Only an explicit disposable loopback PostgreSQL database is permitted.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

from check_attachment_storage_encoding_migration import SOURCE_MODELS, fingerprints, seed_legacy
from check_document_control_migrations import assert_rejected, preceding_state, provision_database


MIGRATION = '0020_document_custom_tag'
DOCUMENT_MODELS = (
    ('sales', 'OpportunityDocument'), ('sales', 'OpportunityDocumentClassification'),
    ('sales', 'OpportunityDocumentClassificationRun'), ('sales', 'OpportunityDocumentClassificationCommand'),
)


def seed_document_control(registry):
    """Retain a reviewed v1, current v2, uncertain v3 and their independent jobs."""
    from django.utils import timezone

    model = registry.get_model
    source_id = seed_legacy(registry)
    uploads = model('sales', 'OpportunityWorkspaceUpload').objects
    source = uploads.get(pk=source_id)
    document = model('sales', 'OpportunityDocument').objects.create(
        id=source.pk, workspace_id=source.workspace_id, folder_key=source.folder_key,
        name=source.name, normalized_name=source.normalized_name,
        root_upload_id=source.pk, head_upload_id=source.pk)
    uploads.filter(pk=source.pk).update(document_id=document.pk)
    second = uploads.create(
        request_id=uuid4(), actor_id=source.actor_id, workspace_id=source.workspace_id,
        folder_key=source.folder_key, name='Retained revised proposal.pdf', size=2048, sha256='a' * 64,
        provider='radai', status='ready', document_id=document.pk, version_number=2,
        previous_upload_id=source.pk, normalized_name=source.normalized_name,
        revision_note='Retained second revision', expected_head_token='b' * 64,
        storage_encoding='gzip', stored_size=111, stored_sha256='c' * 64,
        storage_fingerprint=source.storage_fingerprint)
    uploads.filter(pk=second.pk).update(
        storage_name=f'sales-opportunity-attachments/{source.workspace.opportunity_id}/{second.pk}/original')
    pending = uploads.create(
        request_id=uuid4(), actor_id=source.actor_id, workspace_id=source.workspace_id,
        folder_key=source.folder_key, name='Retained uncertain revision.pdf', size=3030, sha256='d' * 64,
        provider='radai', status='uncertain', document_id=document.pk, version_number=3,
        previous_upload_id=second.pk, normalized_name=source.normalized_name,
        revision_note='Retained pending revision', expected_head_token='e' * 64,
        error_code='private_storage_unavailable', storage_fingerprint=source.storage_fingerprint)
    document.head_upload_id, document.pending_upload_id = second.pk, pending.pk
    document.save(update_fields=['head_upload', 'pending_upload'])
    classification = model('sales', 'OpportunityDocumentClassification').objects.create(
        document_id=document.pk, confirmed_type='technical_proposal', revision=4, updated_by_id=source.actor_id)
    runs = model('sales', 'OpportunityDocumentClassificationRun').objects
    runs.create(upload_id=source.pk, requested_by_id=source.actor_id, status='completed',
                source_sha256=source.sha256, source_identity='f' * 64, attempts=1,
                suggested_type='commercial_proposal', origin='rule',
                evidence=[{'source': 'filename', 'matched_text': 'Retained commercial proposal'}])
    runs.create(upload_id=second.pk, requested_by_id=source.actor_id, status='queued',
                source_sha256=second.sha256, source_identity='1' * 64, next_attempt_at=timezone.now())
    model('sales', 'OpportunityDocumentClassificationCommand').objects.create(
        document_id=document.pk, upload_id=source.pk, actor_id=source.actor_id,
        request_id=uuid4(), request_hash='2' * 64, kind='confirm')
    return classification.pk


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_procurement_postgresql_test'
    os.environ.setdefault('RADAI_DOCUMENT_MIGRATION_DATABASE', 'document_control_migration_verify_customtag')
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
        raise RuntimeError('Refusing a populated verification database; use a fresh synthetic name.')
    migration = importlib.import_module(f'apps.sales.migrations.{MIGRATION}').Migration(MIGRATION, 'sales')
    call_command('migrate', run_syncdb=True, interactive=False, verbosity=0)
    current = ProjectState.from_apps(apps)
    baseline = preceding_state(current, [migration])
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    classification_id = seed_document_control(baseline.apps)
    fields = {key: [field.attname for field in baseline.apps.get_model(*key)._meta.local_fields]
              for key in (*SOURCE_MODELS, *DOCUMENT_MODELS)}
    original = fingerprints(baseline.apps, fields)

    with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No Graph during DDL')), \
            patch('apps.sales.attachment_storage.attachment_storage', side_effect=AssertionError('No storage during DDL')), \
            patch('apps.sales.document_classification.queue_classification', side_effect=AssertionError('No job backfill')):
        with connection.schema_editor() as editor:
            migration.apply(baseline.clone(), editor)
    assert fingerprints(current.apps, fields) == original
    classifications = current.apps.get_model('sales', 'OpportunityDocumentClassification').objects
    assert classifications.get(pk=classification_id).custom_tag == ''
    print('PASS: 0020 forward preserved all original fields across 15 source models; existing tag blank; no job backfill', flush=True)

    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    assert fingerprints(baseline.apps, fields) == original
    with connection.schema_editor() as editor:
        migration.apply(baseline.clone(), editor)
    assert fingerprints(current.apps, fields) == original
    print('PASS: blank-tag reverse/reapply retains document versions, pending storage, confirmed types, jobs and review evidence', flush=True)

    assert_rejected(connection, lambda: classifications.filter(pk=classification_id).update(custom_tag='x' * 81), 'custom tag beyond 80 characters')
    assert_rejected(connection, lambda: classifications.filter(pk=classification_id).update(custom_tag=None), 'null custom tag')
    label = 'Tender follow-up — retained ✓'
    classifications.filter(pk=classification_id).update(custom_tag=label)
    try:
        with connection.schema_editor() as editor:
            migration.unapply(baseline.clone(), editor)
    except RuntimeError as error:
        assert 'tag' in str(error).lower()
    else:
        raise AssertionError('Reverse discarded a nonempty custom tag.')
    assert classifications.get(pk=classification_id).custom_tag == label
    assert fingerprints(current.apps, fields) == original
    print('PASS: PostgreSQL length/null constraints and populated reverse guard preserve custom labels and prior evidence', flush=True)

    classifications.filter(pk=classification_id).update(custom_tag='')
    with connection.schema_editor() as editor:
        migration.unapply(baseline.clone(), editor)
    assert fingerprints(baseline.apps, fields) == original
    with connection.schema_editor() as editor:
        migration.apply(baseline.clone(), editor)
    assert classifications.get(pk=classification_id).custom_tag == ''
    assert fingerprints(current.apps, fields) == original
    print('PASS: explicitly cleared tag permits reverse/reapply without altering prior classification/version records', flush=True)
    print('BOUNDARY: actual isolated PostgreSQL 0020 additive DDL; no 0018/0019 rerun, production operation or live document/provider access', flush=True)


if __name__ == '__main__':
    main()
