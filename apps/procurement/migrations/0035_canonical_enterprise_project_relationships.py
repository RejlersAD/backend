from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


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
