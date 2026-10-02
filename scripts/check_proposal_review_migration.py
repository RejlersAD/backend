"""Exercise Sales 0014 DDL using its historical predecessor on disposable PG.

This creates the declared 0013 dependency state directly, then executes 0014.
It is not a fresh replay of every migration. No application database is opened.
The shared provisioner requires explicit loopback test credentials, a distinct
synthetic database name, and this script refuses a populated database.
"""
from __future__ import annotations

from datetime import date
from importlib import import_module
import os
from pathlib import Path
import sys
from uuid import uuid4

from check_document_control_migrations import provision_database


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_procurement_postgresql_test'
    os.environ.setdefault('RADAI_DOCUMENT_MIGRATION_DATABASE', 'document_control_migration_verify_review')
    database = provision_database()
    from django.conf import settings
    settings.DATABASES['default']['NAME'] = database
    settings.DATABASES['default']['TEST']['NAME'] = database
    import django
    django.setup()
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader
    from django.test import override_settings

    assert connection.vendor == 'postgresql'
    assert connection.settings_dict['HOST'] == '127.0.0.1'
    assert connection.settings_dict['NAME'] == database
    if connection.introspection.table_names():
        raise RuntimeError('Refusing a populated verification database; use a fresh synthetic name.')
    with override_settings(MIGRATION_MODULES={}):
        before = MigrationLoader(None).project_state([('sales', '0013_private_opportunity_attachments')])
    with connection.schema_editor() as editor:
        for model in before.apps.get_models():
            if model._meta.managed and not model._meta.proxy:
                editor.create_model(model)

    model = before.apps.get_model
    actor = model('users', 'User').objects.create(
        username='synthetic-review-migration', email='review-migration@example.test', password='!')
    client = model('sales', 'Client').objects.create(
        client_code='REVIEW-MIGRATION', company_name='Synthetic migration client', industry_type='other')
    deal = model('sales', 'Deal').objects.create(
        deal_code='Q-102101', deal_name='Synthetic migration opportunity', client_id=client.pk, owner_id=actor.pk)
    quote = model('sales', 'Quote').objects.create(
        quote_number='REVIEW-MIGRATION-1', deal_id=deal.pk, client_id=client.pk,
        subtotal='1000.00', total_amount='1000.00', valid_until=date(2026, 12, 1), prepared_by_id=actor.pk)
    workspace = model('sales', 'OpportunityWorkspace').objects.create(
        opportunity_id=deal.pk, requested_by_id=actor.pk)
    upload = model('sales', 'OpportunityWorkspaceUpload').objects.create(
        workspace_id=workspace.pk, request_id=uuid4(), actor_id=actor.pk,
        folder_key='proposal', name='Existing.pdf', size=5, sha256='b' * 64,
        status='ready', provider='radai', storage_name='synthetic-private-object')
    names = ('Client', 'Deal', 'Quote', 'OpportunityWorkspace', 'OpportunityWorkspaceUpload')
    snapshots = {name: list(model('sales', name).objects.values()) for name in names}
    migration = import_module('apps.sales.migrations.0014_proposal_review').Migration('0014_proposal_review', 'sales')
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    for name, expected in snapshots.items():
        assert list(after.apps.get_model('sales', name).objects.values()) == expected
    with connection.schema_editor() as editor:
        migration.unapply(before.clone(), editor)
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    print('PASS: actual 0014 forward/empty reverse/reapply; five existing source tables unchanged', flush=True)

    document_model = after.apps.get_model('sales', 'ProposalReviewDocument')
    comment_model = after.apps.get_model('sales', 'ProposalReviewComment')
    command_model = after.apps.get_model('sales', 'ProposalReviewCommand')
    document = document_model.objects.create(
        quote_id=quote.pk, attachment_id=upload.pk, revision=1, name=upload.name,
        sha256=upload.sha256, size=upload.size, page_count=1, created_by_id=actor.pk)
    comment_model.objects.create(
        document_id=document.pk, body='Synthetic migration feedback', author_id=actor.pk,
        kind='required_change', page_number=1)
    command_model.objects.create(
        quote_id=quote.pk, document_id=document.pk, actor_id=actor.pk,
        request_id=uuid4(), action='submit', payload_hash='c' * 64, outcome='request_changes')
    try:
        with connection.schema_editor() as editor:
            migration.unapply(before.clone(), editor)
    except RuntimeError as exc:
        assert 'evidence' in str(exc).lower()
    else:
        raise AssertionError('Reversal must preserve saved review evidence.')
    assert (document_model.objects.count(), comment_model.objects.count(), command_model.objects.count()) == (1, 1, 1)
    for name, expected in snapshots.items():
        assert list(after.apps.get_model('sales', name).objects.values()) == expected
    with connection.cursor() as cursor:
        document_constraints = connection.introspection.get_constraints(cursor, document_model._meta.db_table)
        command_constraints = connection.introspection.get_constraints(cursor, command_model._meta.db_table)
    assert document_constraints['sales_review_document_revision']['unique']
    assert command_constraints['sales_review_request_identity']['unique']
    assert any(item['columns'] == ['attachment_id'] and item.get('foreign_key')
               for item in document_constraints.values())
    print('PASS: populated review reversal refused; evidence, attachment FK and revision/retry uniqueness retained', flush=True)
    print('BOUNDARY: additive PostgreSQL DDL on synthetic data; no full history or production claim', flush=True)


if __name__ == '__main__':
    main()
