from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def ensure_enterprise_project_id_uniqueness(apps, schema_editor):
    """Restore the legacy core_project ownership key before adding FKs.

    Some synchronized production databases retained the ``id`` values but not
    the primary-key index declared by the historical core migration.  Do not
    rewrite canonical project identifiers because other legacy tables may
    already refer to them logically; fail clearly if the data itself is
    ambiguous, otherwise restore the missing uniqueness constraint.
    """
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    table_name = 'core_project'
    if table_name not in set(connection.introspection.table_names()):
        return

    quote_name = connection.ops.quote_name
    table = quote_name(table_name)
    with connection.cursor() as cursor:
        cursor.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
        cursor.execute(
            f'''
            SELECT
                COUNT(*) FILTER (WHERE id IS NULL),
                COUNT(*)
            FROM (
                SELECT id, COUNT(*) AS occurrences
                FROM {table}
                GROUP BY id
            ) AS grouped
            WHERE id IS NULL OR occurrences > 1
            ''',
        )
        null_groups, invalid_groups = cursor.fetchone()
        if invalid_groups:
            duplicate_groups = invalid_groups - null_groups
            raise RuntimeError(
                'Cannot restore core_project.id uniqueness safely: '
                f'{null_groups} null id group(s) and '
                f'{duplicate_groups} duplicate id group(s) require manual reconciliation.'
            )

        constraints = connection.introspection.get_constraints(cursor, table_name)
        id_is_unique = any(
            constraint.get('unique') and constraint.get('columns') == ['id']
            for constraint in constraints.values()
        )
        if not id_is_unique:
            schema_editor.execute(
                f'CREATE UNIQUE INDEX {quote_name("core_project_id_repair_uniq")} '
                f'ON {table} ({quote_name("id")})'
            )

        cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [table_name, 'id'])
        sequence_name = cursor.fetchone()[0]
        if sequence_name:
            cursor.execute(
                f'''
                SELECT setval(
                    %s::regclass,
                    COALESCE(MAX(id), 1),
                    EXISTS (SELECT 1 FROM {table})
                )
                FROM {table}
                ''',
                [sequence_name],
            )


def normalize(value):
    return ' '.join(str(value or '').strip().casefold().split())


def requisition_codes(row):
    codes = []
    if normalize(row.project):
        codes.append(str(row.project).strip())
    for detail in row.project_details if isinstance(row.project_details, list) else []:
        if not isinstance(detail, dict):
            continue
        code = detail.get('project_number') or detail.get('project_code') or detail.get('code')
        if normalize(code):
            codes.append(str(code).strip())
    return list(dict.fromkeys(codes))


def unique_candidate(candidates):
    ids = {str(candidate) for candidate in candidates if candidate is not None}
    return next(iter(ids)) if len(ids) == 1 else None


def backfill_canonical_projects(apps, schema_editor):
    EnterpriseProject = apps.get_model('core', 'Project')
    ProcurementProject = apps.get_model('procurement', 'Project')
    PurchaseRequisition = apps.get_model('procurement', 'PurchaseRequisition')
    PurchaseOrder = apps.get_model('procurement', 'PurchaseOrder')

    enterprise_index = defaultdict(list)
    for project in EnterpriseProject.objects.filter(is_deleted=False).only('id', 'code'):
        key = normalize(project.code)
        if key:
            enterprise_index[key].append(project.pk)

    master_candidates = {}
    for project in ProcurementProject.objects.all().iterator(chunk_size=200):
        candidate = unique_candidate(enterprise_index.get(normalize(project.project_number), []))
        if candidate:
            ProcurementProject.objects.filter(pk=project.pk).update(enterprise_project_id=candidate)
            master_candidates[str(project.pk)] = candidate

    pr_candidates = {}
    for requisition in PurchaseRequisition.objects.all().iterator(chunk_size=200):
        candidates = []
        for code in requisition_codes(requisition):
            candidates.extend(enterprise_index.get(normalize(code), []))
        for detail in requisition.project_details if isinstance(requisition.project_details, list) else []:
            project_id = detail.get('project_id') if isinstance(detail, dict) else None
            if project_id and str(project_id) in master_candidates:
                candidates.append(master_candidates[str(project_id)])
        candidate = unique_candidate(candidates)
        if candidate:
            PurchaseRequisition.objects.filter(pk=requisition.pk).update(enterprise_project_id=candidate)
            pr_candidates[str(requisition.pk)] = candidate

    for order in PurchaseOrder.objects.all().iterator(chunk_size=200):
        candidates = []
        if order.project_id and str(order.project_id) in master_candidates:
            candidates.append(master_candidates[str(order.project_id)])
        candidates.extend(enterprise_index.get(normalize(order.project_number), []))
        if order.pr_reference_id and str(order.pr_reference_id) in pr_candidates:
            candidates.append(pr_candidates[str(order.pr_reference_id)])
        candidate = unique_candidate(candidates)
        if candidate:
            PurchaseOrder.objects.filter(pk=order.pk).update(enterprise_project_id=candidate)


def clear_canonical_projects(apps, schema_editor):
    apps.get_model('procurement', 'PurchaseOrder').objects.update(enterprise_project=None)
    apps.get_model('procurement', 'PurchaseRequisition').objects.update(enterprise_project=None)
    apps.get_model('procurement', 'Project').objects.update(enterprise_project=None)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0008_document_documentaccesslog'),
        ('procurement', '0034_remap_approval_users_and_backfill_notifications'),
    ]

    operations = [
        migrations.RunPython(
            ensure_enterprise_project_id_uniqueness,
            # A repaired ownership key may be used by other applications.
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name='project',
            name='enterprise_project',
            field=models.OneToOneField(
                blank=True,
                help_text='Authoritative enterprise Project used by Project Control, Finance, and Procurement.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='procurement_project',
                to='core.project',
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='enterprise_project',
            field=models.ForeignKey(
                blank=True,
                help_text='Canonical project when this requisition resolves to exactly one enterprise project.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='purchase_requisitions',
                to='core.project',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='enterprise_project',
            field=models.ForeignKey(
                blank=True,
                help_text='Authoritative enterprise Project for cross-department cost reporting.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='purchase_orders',
                to='core.project',
            ),
        ),
        migrations.RunPython(backfill_canonical_projects, clear_canonical_projects),
    ]
