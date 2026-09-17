"""Canonical Project reconciliation for Procurement records.

The enterprise project in ``apps.core`` is authoritative.  Procurement's
legacy project registry and text/JSON references remain available during the
migration. Only exact, case-insensitive project codes are linked automatically;
review suggestions can explain weaker evidence without changing any link.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.project_models import Project as EnterpriseProject
from apps.procurement.models import (
    Project,
    ProjectRelationshipResolution,
    PurchaseOrder,
    PurchaseRequisition,
)


EXPECTED_PROJECT_UNSET = object()


def _project_brief(project):
    return {'id': str(project.pk), 'code': project.code, 'name': project.name} if project else None


def _project_choice(project):
    return {
        **_project_brief(project), 'status': project.status,
        'client_name': project.client_name, 'currency': project.currency,
    }


def _identifier(record_type, row):
    return str(getattr(row, {
        'procurement_project': 'project_number', 'purchase_requisition': 'pr_number',
        'purchase_order': 'po_number', 'invoice': 'invoice_number',
    }[record_type], '') or row.pk)


def _reason(value, *, required=False):
    reason = str(value or '').strip()
    if required and not reason:
        raise ValidationError({'reason': 'Explain why this record is being saved as an exception.'})
    if len(reason) > 500:
        raise ValidationError({'reason': 'Reason cannot exceed 500 characters.'})
    return reason


def _suggest_projects(record_type, row, projects, masters, orders_by_number):
    """Explain real reference evidence without auto-linking or invented confidence."""
    evidence = {}
    strengths = {'high': 3, 'medium': 2, 'low': 1}

    def add(project, strength, reason):
        if project is None or project.is_deleted:
            return
        item = evidence.setdefault(str(project.pk), {
            **_project_choice(project), 'match_strength': strength, 'reasons': [],
        })
        if strengths[strength] > strengths[item['match_strength']]:
            item['match_strength'] = strength
        if reason not in item['reasons']:
            item['reasons'].append(reason)

    codes, names, text = [], [], ''
    if record_type == 'procurement_project':
        codes, names = [row.project_number], [row.project_name]
        text = f'{row.project_number} {row.project_name}'
    elif record_type == 'purchase_requisition':
        codes = extract_requisition_project_codes(row.project, row.project_details)
        text = f'{row.project} {row.project_department} {row.title} {row.product_service}'
        names = [row.project_department]
        for detail in row.project_details if isinstance(row.project_details, list) else []:
            if not isinstance(detail, dict):
                continue
            names.append(detail.get('project_name') or '')
            master = masters.get(str(detail.get('project_id') or ''))
            if master and master.enterprise_project_id:
                add(master.enterprise_project, 'high', f'Referenced procurement project {master.project_number} is linked here.')
    elif record_type == 'purchase_order':
        codes = [row.project_number]
        text = f'{row.project_number} {row.title}'
        if row.project_id:
            names.append(row.project.project_name)
            if row.project.enterprise_project_id:
                add(row.project.enterprise_project, 'high', f'Linked procurement project {row.project.project_number}.')
        if row.pr_reference_id and row.pr_reference.enterprise_project_id:
            add(row.pr_reference.enterprise_project, 'high', f'Originating recommendation {row.pr_reference.pr_number} is linked here.')
    elif record_type == 'invoice':
        for allocation in row.po_allocations.all():
            order = allocation.purchase_order
            if order.enterprise_project_id:
                add(order.enterprise_project, 'high', f'Existing PO allocation references {order.po_number}; invoice matching still requires review.')
        for order in orders_by_number.get(normalize_project_code(row.po_reference_text), []):
            add(order.enterprise_project, 'high', f'Exact invoice PO reference: {order.po_number}. Match the invoice to this PO before project reconciliation.')

    normalized_codes = {normalize_project_code(code) for code in codes if code}
    normalized_names = {normalize_project_code(name) for name in names if name}
    for project in projects:
        if normalize_project_code(project.code) in normalized_codes:
            add(project, 'high', f'Exact project code reference: {project.code}.')
        elif text and re.search(r'(?<![\w-])' + re.escape(project.code) + r'(?![\w-])', text, re.IGNORECASE):
            add(project, 'medium', f'Project code {project.code} appears in the record description.')
        if normalize_project_code(project.name) in normalized_names:
            add(project, 'medium', f'Exact project name reference: {project.name}.')
        elif text:
            ignored = {'project', 'general', 'internal', 'services', 'service', 'engineering', 'the', 'and', 'for'}
            project_words = set(re.findall(r'[a-z0-9]{3,}', project.name.lower())) - ignored
            matching = project_words & set(re.findall(r'[a-z0-9]{3,}', text.lower()))
            if len(matching) >= 2:
                add(project, 'low', f'Shared project-name words: {", ".join(sorted(matching))}. Confirm against source documents.')
    return sorted(evidence.values(), key=lambda item: (
        -strengths[item['match_strength']], -len(item['reasons']), item['code'], item['id'],
    ))[:3]


def normalize_project_code(value) -> str:
    """Normalize harmless formatting without changing project-code meaning."""
    return ' '.join(str(value or '').strip().casefold().split())


def extract_requisition_project_codes(project='', project_details=None) -> list[str]:
    """Return explicit project-code fields from the legacy PR payload."""
    codes = []
    if normalize_project_code(project):
        codes.append(str(project).strip())
    for detail in project_details if isinstance(project_details, list) else []:
        if not isinstance(detail, dict):
            continue
        code = detail.get('project_number') or detail.get('project_code') or detail.get('code')
        if normalize_project_code(code):
            codes.append(str(code).strip())
    return list(dict.fromkeys(codes))


def _enterprise_index():
    index = defaultdict(list)
    for project in EnterpriseProject.objects.filter(is_deleted=False).only('id', 'code', 'name'):
        key = normalize_project_code(project.code)
        if key:
            index[key].append(project)
    return index


def _unique_candidate(candidates: Iterable[EnterpriseProject]):
    by_id = {str(candidate.pk): candidate for candidate in candidates if candidate is not None}
    if len(by_id) == 1:
        return next(iter(by_id.values())), 'exact'
    if len(by_id) > 1:
        return None, 'multiple_projects'
    return None, 'no_exact_match'


def resolve_enterprise_project_by_code(code, *, index=None):
    """Resolve one explicit project code without fuzzy or substring matching."""
    index = index or _enterprise_index()
    return _unique_candidate(index.get(normalize_project_code(code), []))


def resolve_requisition_enterprise_project(*, project='', project_details=None, index=None):
    """Resolve a PR only when all recognized references identify one project."""
    index = index or _enterprise_index()
    candidates = []
    for code in extract_requisition_project_codes(project, project_details):
        candidates.extend(index.get(normalize_project_code(code), []))

    project_ids = []
    for detail in project_details if isinstance(project_details, list) else []:
        if isinstance(detail, dict) and detail.get('project_id'):
            try:
                project_ids.append(UUID(str(detail['project_id'])))
            except (TypeError, ValueError, AttributeError):
                # Core-project UI identities and legacy free text are not
                # procurement-master UUIDs and must not break PR validation.
                continue
    if project_ids:
        for procurement_project in Project.objects.filter(pk__in=project_ids).select_related('enterprise_project'):
            if procurement_project.enterprise_project_id:
                candidates.append(procurement_project.enterprise_project)

    return _unique_candidate(candidates)


def resolve_order_enterprise_project(*, project=None, project_number='', requisition=None, index=None):
    """Resolve PO linkage from its master project, code, and originating PR."""
    index = index or _enterprise_index()
    candidates = []
    if project is not None and getattr(project, 'enterprise_project_id', None):
        candidates.append(project.enterprise_project)
    key = normalize_project_code(project_number)
    if key:
        candidates.extend(index.get(key, []))
    if requisition is not None and getattr(requisition, 'enterprise_project_id', None):
        candidates.append(requisition.enterprise_project)
    return _unique_candidate(candidates)


def build_project_relationship_report(*, apply=False, sample_limit=50):
    """Report and optionally apply safe, unambiguous canonical links."""
    index = _enterprise_index()
    report = {
        'mode': 'apply' if apply else 'report',
        'matching_rule': 'exact_case_insensitive_project_code',
        'enterprise_projects': EnterpriseProject.objects.filter(is_deleted=False).count(),
        'procurement_projects': {},
        'purchase_requisitions': {},
        'purchase_orders': {},
        'unresolved': [],
        'changes_applied': 0,
    }

    def unresolved(kind, object_id, reference, reason):
        if len(report['unresolved']) < sample_limit:
            report['unresolved'].append({
                'record_type': kind,
                'id': str(object_id),
                'reference': reference,
                'reason': reason,
            })

    context = transaction.atomic() if apply else nullcontext()
    with context:
        master_candidates = {}
        masters = list(Project.objects.select_related('enterprise_project').all())
        master_linked_before = sum(bool(row.enterprise_project_id) for row in masters)
        master_resolved = 0
        for row in masters:
            candidate = row.enterprise_project
            reason = 'existing_link' if candidate else 'no_exact_match'
            if candidate is None:
                candidate, reason = _unique_candidate(
                    index.get(normalize_project_code(row.project_number), [])
                )
            if candidate:
                master_resolved += 1
                master_candidates[str(row.pk)] = candidate
                if apply and row.enterprise_project_id != candidate.pk:
                    row.enterprise_project = candidate
                    row.save(update_fields=['enterprise_project', 'updated_at'])
                    report['changes_applied'] += 1
            else:
                unresolved('procurement_project', row.pk, row.project_number, reason)

        requisitions = list(PurchaseRequisition.objects.select_related('enterprise_project').all())
        pr_linked_before = sum(bool(row.enterprise_project_id) for row in requisitions)
        pr_resolved = 0
        pr_candidates = {}
        for row in requisitions:
            candidate = row.enterprise_project
            reason = 'existing_link' if candidate else None
            if candidate is None:
                candidates = []
                for code in extract_requisition_project_codes(row.project, row.project_details):
                    candidates.extend(index.get(normalize_project_code(code), []))
                for detail in row.project_details if isinstance(row.project_details, list) else []:
                    project_id = detail.get('project_id') if isinstance(detail, dict) else None
                    if project_id and str(project_id) in master_candidates:
                        candidates.append(master_candidates[str(project_id)])
                candidate, reason = _unique_candidate(candidates)
            if candidate:
                pr_resolved += 1
                pr_candidates[str(row.pk)] = candidate
                if apply and row.enterprise_project_id != candidate.pk:
                    row.enterprise_project = candidate
                    row.save(update_fields=['enterprise_project', 'updated_at'])
                    report['changes_applied'] += 1
            else:
                unresolved(
                    'purchase_requisition', row.pk,
                    extract_requisition_project_codes(row.project, row.project_details), reason,
                )

        orders = list(PurchaseOrder.objects.select_related(
            'enterprise_project', 'project__enterprise_project', 'pr_reference__enterprise_project'
        ).all())
        po_linked_before = sum(bool(row.enterprise_project_id) for row in orders)
        po_resolved = 0
        for row in orders:
            candidate = row.enterprise_project
            reason = 'existing_link' if candidate else None
            if candidate is None:
                candidates = []
                if row.project_id:
                    master_candidate = master_candidates.get(str(row.project_id))
                    if master_candidate:
                        candidates.append(master_candidate)
                candidates.extend(index.get(normalize_project_code(row.project_number), []))
                if row.pr_reference_id:
                    pr_candidate = pr_candidates.get(str(row.pr_reference_id))
                    if pr_candidate:
                        candidates.append(pr_candidate)
                candidate, reason = _unique_candidate(candidates)
            if candidate:
                po_resolved += 1
                if apply and row.enterprise_project_id != candidate.pk:
                    row.enterprise_project = candidate
                    row.save(update_fields=['enterprise_project', 'updated_at'])
                    report['changes_applied'] += 1
            else:
                unresolved('purchase_order', row.pk, row.project_number, reason)

        report['procurement_projects'] = {
            'total': len(masters), 'linked_before': master_linked_before,
            'resolvable': master_resolved, 'unresolved': len(masters) - master_resolved,
        }
        report['purchase_requisitions'] = {
            'total': len(requisitions), 'linked_before': pr_linked_before,
            'resolvable': pr_resolved, 'unresolved': len(requisitions) - pr_resolved,
        }
        report['purchase_orders'] = {
            'total': len(orders), 'linked_before': po_linked_before,
            'resolvable': po_resolved, 'unresolved': len(orders) - po_resolved,
        }

    report['unresolved_sample_count'] = len(report['unresolved'])
    return report


def build_project_reconciliation_payload(*, sample_limit=1000):
    """Return the operator-facing reconciliation queue and canonical choices."""
    report = build_project_relationship_report(apply=False, sample_limit=sample_limit)
    master_by_id = {
        str(row.pk): row
        for row in Project.objects.select_related('enterprise_project')
    }
    pr_by_id = {
        str(row.pk): row
        for row in PurchaseRequisition.objects.select_related('enterprise_project')
    }
    po_by_id = {
        str(row.pk): row
        for row in PurchaseOrder.objects.select_related(
            'enterprise_project', 'project__enterprise_project', 'pr_reference__enterprise_project',
        )
    }

    rows = []
    for item in report['unresolved']:
        record_id = item['id']
        record_type = item['record_type']
        row = None
        identifier = ''
        title = ''
        amount = None
        currency = ''
        if record_type == 'procurement_project':
            row = master_by_id.get(record_id)
            if row:
                identifier, title = row.project_number, row.project_name
        elif record_type == 'purchase_requisition':
            row = pr_by_id.get(record_id)
            if row:
                identifier = row.pr_number
                title = row.title or row.product_service or row.project_department
                amount = float(row.total_price or row.estimated_budget or 0)
                currency = row.currency
        elif record_type == 'purchase_order':
            row = po_by_id.get(record_id)
            if row:
                identifier, title = row.po_number, row.title
                amount = float(row.total_amount or 0)
                currency = row.currency
        if row is None:
            continue
        rows.append({
            **item,
            'identifier': identifier,
            'title': title,
            'amount': amount,
            'currency': currency,
            'created_at': row.created_at.isoformat(),
            'current_project': _project_brief(row.enterprise_project),
        })

    # Finance invoices cannot be safely connected to a Project directly.  They
    # must be matched to a canonical PO first so receipt and value evidence is
    # retained for the three-way check.
    invoice_summary = {'total': 0, 'linked_before': 0, 'resolvable': 0, 'unresolved': 0}
    purchase_order_choices = []
    invoice_by_id = {}
    try:
        from apps.finance.models import Invoice, InvoiceMatchStatus

        invoices = Invoice.objects.prefetch_related('po_allocations__purchase_order__enterprise_project')
        invoice_summary['total'] = invoices.count()
        verified_ids = invoices.filter(
            po_allocations__match_status=InvoiceMatchStatus.VERIFIED,
        ).values_list('pk', flat=True).distinct()
        invoice_summary['linked_before'] = len(verified_ids)
        unresolved_invoices = invoices.exclude(pk__in=verified_ids)
        invoice_summary['unresolved'] = unresolved_invoices.count()
        for invoice in unresolved_invoices[:sample_limit]:
            invoice_by_id[str(invoice.pk)] = invoice
            allocations = list(invoice.po_allocations.all())
            invoice_total = invoice.total_amount or invoice.amount or Decimal('0')
            allocated_total = sum((allocation.allocated_amount for allocation in allocations), Decimal('0'))
            rows.append({
                'record_type': 'invoice',
                'id': str(invoice.pk),
                'reference': invoice.po_reference_text or '',
                'reason': 'no_verified_po' if allocations else 'missing_po_match',
                'identifier': invoice.invoice_number,
                'title': invoice.vendor_name or 'Unknown vendor',
                'amount': float(invoice.total_amount or invoice.amount or 0),
                'currency': invoice.currency,
                'created_at': invoice.created_at.isoformat(),
                'current_project': None,
                'allocation': None,
                'invoice_match': {
                    'status': invoice.match_status,
                    'allocated_amount': float(allocated_total),
                    'remaining_amount': float(max(invoice_total - allocated_total, Decimal('0'))),
                    'existing_pos': [allocation.purchase_order.po_number for allocation in allocations],
                    'exception_codes': sorted({
                        code for allocation in allocations for code in (allocation.exception_codes or [])
                    }),
                },
            })

        purchase_order_choices = [
            {
                'id': str(order.pk),
                'po_number': order.po_number,
                'title': order.title,
                'project_id': str(order.enterprise_project_id),
                'project_code': order.enterprise_project.code,
                'vendor_id': str(order.vendor_id),
                'vendor_name': order.vendor.name,
                'amount': float(order.total_amount or 0),
                'currency': order.currency,
                'has_accepted_receipt': order.receipts.filter(
                    status__in=('accepted', 'partial'),
                ).exists(),
            }
            for order in PurchaseOrder.objects.filter(
                enterprise_project__isnull=False,
            ).exclude(status='cancelled').select_related(
                'enterprise_project', 'vendor',
            ).order_by('-approved_at', '-created_at')
        ]
    except (DatabaseError, ImportError, RuntimeError):
        pass

    projects = list(EnterpriseProject.objects.filter(is_deleted=False).order_by('code'))
    canonical_projects = [_project_choice(project) for project in projects]
    record_maps = {
        'procurement_project': master_by_id, 'purchase_requisition': pr_by_id,
        'purchase_order': po_by_id, 'invoice': invoice_by_id,
    }
    orders_by_number = defaultdict(list)
    for order in po_by_id.values():
        if order.enterprise_project_id and order.status != 'cancelled':
            orders_by_number[normalize_project_code(order.po_number)].append(order)
    latest_audit = {}
    for audit in ProjectRelationshipResolution.objects.filter(
        record_id__in=[row['id'] for row in rows],
    ).select_related('resolved_by').order_by('-created_at', '-id'):
        latest_audit.setdefault((audit.record_type, str(audit.record_id)), audit)
    currency_totals = defaultdict(lambda: {'amount': Decimal('0'), 'record_count': 0})
    for item in rows:
        source = record_maps[item['record_type']][item['id']]
        item['suggested_projects'] = _suggest_projects(
            item['record_type'], source, projects, master_by_id, orders_by_number,
        )
        audit = latest_audit.get((item['record_type'], item['id']))
        item['exception'] = {
            'reason': audit.reason, 'created_at': audit.created_at.isoformat(),
            'resolved_by': audit.resolved_by.get_full_name() if audit.resolved_by else None,
        } if audit and audit.resolution == 'exception' else None
        if item['amount'] is not None:
            totals = currency_totals[str(item['currency'] or '').strip().upper() or 'UNKNOWN']
            source_amount = (
                source.total_price or source.estimated_budget or Decimal('0')
                if item['record_type'] == 'purchase_requisition' else
                source.total_amount or (source.amount if item['record_type'] == 'invoice' else Decimal('0')) or Decimal('0')
            )
            totals['amount'] += source_amount
            totals['record_count'] += 1
    try:
        from apps.project_control.models import CostAllocation
        allocation_index = {}
        for allocation in CostAllocation.objects.filter(
            is_deleted=False,
            source_type__in=['purchase_requisition', 'purchase_order'],
        ).values('source_type', 'source_id', 'status').annotate(total=Sum('amount')):
            key = (allocation['source_type'], allocation['source_id'])
            allocation_index.setdefault(key, {})[allocation['status']] = float(allocation['total'] or 0)
        for row in rows:
            if row['record_type'] not in ('purchase_requisition', 'purchase_order'):
                row['allocation'] = None
                continue
            totals = allocation_index.get((row['record_type'], row['id']), {})
            approved = totals.get('approved', 0)
            draft = totals.get('draft', 0)
            source_amount = float(row.get('amount') or 0)
            row['allocation'] = {
                'approved_amount': approved,
                'draft_amount': draft,
                'source_amount': source_amount,
                'remaining_amount': max(source_amount - approved - draft, 0),
                'status': 'fully_allocated' if source_amount > 0 and approved >= source_amount else (
                    'partially_allocated' if approved or draft else 'unallocated'
                ),
            }
    except (DatabaseError, ImportError, RuntimeError):
        for row in rows:
            row['allocation'] = None
    recent_audits = list(ProjectRelationshipResolution.objects.select_related(
        'enterprise_project', 'previous_enterprise_project', 'resolved_by',
    )[:20])
    # Resolved invoices have left the queue but still need readable audit labels.
    if any(audit.record_type == 'invoice' for audit in recent_audits):
        from apps.finance.models import Invoice
        invoice_by_id.update({str(invoice.pk): invoice for invoice in Invoice.objects.filter(
            pk__in=[audit.record_id for audit in recent_audits
                    if audit.record_type == 'invoice' and str(audit.record_id).isdigit()],
        )})
    recent_resolutions = [
        {
            'id': str(row.pk),
            'record_type': row.record_type,
            'record_id': str(row.record_id),
            'record_identifier': _identifier(row.record_type, record_maps[row.record_type][str(row.record_id)])
                if str(row.record_id) in record_maps.get(row.record_type, {}) else str(row.record_id),
            'enterprise_project_id': str(row.enterprise_project_id) if row.enterprise_project_id else None,
            'enterprise_project_code': row.enterprise_project.code if row.enterprise_project else None,
            'previous_enterprise_project_code': row.previous_enterprise_project.code
                if row.previous_enterprise_project else None,
            'resolution': row.resolution,
            'reason': row.reason,
            'resolved_by': row.resolved_by.get_full_name() if row.resolved_by else None,
            'created_at': row.created_at.isoformat(),
        }
        for row in recent_audits
    ]
    return {
        'generated_at': timezone.now().isoformat(),
        'matching_rule': report['matching_rule'],
        'summary': {
            'enterprise_projects': report['enterprise_projects'],
            'procurement_projects': report['procurement_projects'],
            'purchase_requisitions': report['purchase_requisitions'],
            'purchase_orders': report['purchase_orders'],
            'invoices': invoice_summary,
            'unresolved_total': sum(
                report[key]['unresolved']
                for key in ('procurement_projects', 'purchase_requisitions', 'purchase_orders')
            ) + invoice_summary['unresolved'],
            'sample_count': len(rows),
            'sample_limit_per_source': sample_limit,
            'sample_complete': len(rows) == sum(
                report[key]['unresolved']
                for key in ('procurement_projects', 'purchase_requisitions', 'purchase_orders')
            ) + invoice_summary['unresolved'],
            'suggested_record_count': sum(bool(row['suggested_projects']) for row in rows),
            'suggestion_scope': 'returned_records',
            'amount_totals_scope': 'returned_records',
            'unresolved_amounts_by_currency': [
                {'currency': currency, 'amount': float(totals['amount']), 'record_count': totals['record_count']}
                for currency, totals in sorted(currency_totals.items())
            ],
        },
        'unresolved': rows,
        'canonical_projects': canonical_projects,
        'purchase_order_choices': purchase_order_choices,
        'recent_resolutions': recent_resolutions,
    }


@transaction.atomic
def resolve_invoice_purchase_order(*, invoice_id, purchase_order_id, allocated_amount, user, reason=''):
    """Manually link an invoice to a PO, then run the existing three-way check."""
    from apps.finance.models import (
        AllocationMatchMethod,
        AuditLog,
        Invoice,
        InvoiceMatchStatus,
        InvoicePurchaseOrderAllocation,
    )
    from apps.finance.services.payables import evaluate_three_way_match
    reason = _reason(reason)

    try:
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError) as exc:
        raise ValidationError({'invoice_id': 'Invoice was not found.'}) from exc
    try:
        order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order_id)
    except (PurchaseOrder.DoesNotExist, ValueError) as exc:
        raise ValidationError({'purchase_order_id': 'Purchase order was not found.'}) from exc
    if not order.enterprise_project_id:
        raise ValidationError({'purchase_order_id': 'Select a PO linked to a canonical project.'})
    if order.status == 'cancelled':
        raise ValidationError({'purchase_order_id': 'A cancelled PO cannot receive an invoice.'})
    if InvoicePurchaseOrderAllocation.objects.filter(invoice=invoice, purchase_order=order).exists():
        raise ValidationError({'purchase_order_id': 'This invoice is already allocated to the selected PO.'})

    try:
        amount = Decimal(str(allocated_amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({'allocated_amount': 'Enter a valid allocation amount.'}) from exc
    if amount <= 0:
        raise ValidationError({'allocated_amount': 'Allocation amount must be greater than zero.'})
    invoice_total = invoice.total_amount or invoice.amount or Decimal('0')
    already_allocated = invoice.po_allocations.aggregate(total=Sum('allocated_amount'))['total'] or Decimal('0')
    remaining = max(invoice_total - already_allocated, Decimal('0'))
    if amount > remaining:
        raise ValidationError({
            'allocated_amount': f'Allocation exceeds the remaining invoice amount ({remaining} {invoice.currency}).'
        })

    allocation = InvoicePurchaseOrderAllocation.objects.create(
        invoice=invoice,
        purchase_order=order,
        allocated_amount=amount,
        currency=invoice.currency or order.currency,
        match_method=AllocationMatchMethod.MANUAL,
        match_status=InvoiceMatchStatus.MANUAL_MATCHED,
        review_notes=str(reason or '').strip(),
        matched_by=user if getattr(user, 'is_authenticated', False) else None,
        matched_at=timezone.now(),
    )
    allocation = evaluate_three_way_match(allocation, user=user)
    AuditLog.objects.create(
        invoice=invoice,
        user=user if getattr(user, 'is_authenticated', False) else None,
        action='manual_po_reconciliation',
        description=f'Invoice manually allocated to PO {order.po_number} and three-way match evaluated.',
        metadata={
            'allocation_id': str(allocation.pk),
            'purchase_order_id': str(order.pk),
            'project_id': str(order.enterprise_project_id),
            'allocated_amount': str(amount),
            'currency': allocation.currency,
            'match_status': allocation.match_status,
            'exception_codes': allocation.exception_codes,
            'reason': str(reason or '').strip(),
        },
    )
    return {
        'allocation_id': str(allocation.pk),
        'invoice_id': str(invoice.pk),
        'purchase_order_id': str(order.pk),
        'purchase_order_number': order.po_number,
        'enterprise_project_id': str(order.enterprise_project_id),
        'match_status': allocation.match_status,
        'exception_codes': allocation.exception_codes,
    }


def _record_resolution(*, record_type, row, enterprise_project, user, reason, resolution):
    ProjectRelationshipResolution.objects.create(
        record_type=record_type,
        record_id=row.pk,
        previous_enterprise_project_id=row.enterprise_project_id,
        enterprise_project=enterprise_project,
        resolved_by=user if getattr(user, 'is_authenticated', False) else None,
        resolution=resolution,
        reason=reason,
    )
    row.enterprise_project = enterprise_project
    row.save(update_fields=['enterprise_project', 'updated_at'])


@transaction.atomic
def resolve_project_relationship(*, record_type, record_id, enterprise_project_id, user, reason='',
                                 expected_project_id=EXPECTED_PROJECT_UNSET):
    """Assign one canonical project and safely propagate through explicit links."""
    reason = _reason(reason)
    model_map = {
        'procurement_project': Project,
        'purchase_requisition': PurchaseRequisition,
        'purchase_order': PurchaseOrder,
    }
    model = model_map.get(record_type) if isinstance(record_type, str) else None
    if model is None:
        raise ValidationError({'record_type': 'Unsupported record type.'})
    try:
        enterprise_project = EnterpriseProject.objects.get(
            pk=enterprise_project_id, is_deleted=False,
        )
        row = model.objects.select_for_update().get(pk=record_id)
    except EnterpriseProject.DoesNotExist as exc:
        raise ValidationError({'enterprise_project_id': 'Canonical project was not found.'}) from exc
    except (model.DoesNotExist, ValueError) as exc:
        raise ValidationError({'record_id': 'Procurement record was not found.'}) from exc

    if expected_project_id is not EXPECTED_PROJECT_UNSET:
        try:
            expected = str(EnterpriseProject._meta.pk.to_python(expected_project_id)) if expected_project_id else None
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValidationError({'expected_project_id': 'Expected project is invalid.'}) from exc
        current = str(row.enterprise_project_id) if row.enterprise_project_id else None
        if current not in {expected, str(enterprise_project.pk)}:
            raise ValidationError({
                'expected_project_id': 'This record was linked to a different project while you were reviewing it. Refresh before saving.',
            })

    if record_type == 'procurement_project':
        conflict = Project.objects.filter(enterprise_project=enterprise_project).exclude(pk=row.pk).first()
        if conflict:
            raise ValidationError({
                'enterprise_project_id': (
                    f'Already assigned to procurement project {conflict.project_number}.'
                )
            })

    changed = row.enterprise_project_id != enterprise_project.pk
    propagated = 0
    if changed:
        try:
            _record_resolution(
                record_type=record_type, row=row, enterprise_project=enterprise_project,
                user=user, reason=reason.strip(), resolution='manual',
            )
        except IntegrityError as exc:
            raise ValidationError({'enterprise_project_id': 'This assignment conflicts with an existing link.'}) from exc

    propagation_reason = f'Propagated from manual {record_type} assignment'
    if record_type == 'procurement_project':
        for order in PurchaseOrder.objects.select_for_update().filter(
            project=row, enterprise_project__isnull=True,
        ):
            _record_resolution(
                record_type='purchase_order', row=order, enterprise_project=enterprise_project,
                user=user, reason=propagation_reason, resolution='propagated',
            )
            propagated += 1
        for requisition in PurchaseRequisition.objects.select_for_update().filter(
            enterprise_project__isnull=True,
        ):
            details = requisition.project_details if isinstance(requisition.project_details, list) else []
            if any(
                isinstance(detail, dict) and str(detail.get('project_id')) == str(row.pk)
                for detail in details
            ):
                _record_resolution(
                    record_type='purchase_requisition', row=requisition,
                    enterprise_project=enterprise_project, user=user,
                    reason=propagation_reason, resolution='propagated',
                )
                propagated += 1
    elif record_type == 'purchase_requisition':
        for order in PurchaseOrder.objects.select_for_update().filter(
            pr_reference=row, enterprise_project__isnull=True,
        ):
            _record_resolution(
                record_type='purchase_order', row=order, enterprise_project=enterprise_project,
                user=user, reason=propagation_reason, resolution='propagated',
            )
            propagated += 1

    return {
        'changed': changed,
        'propagated': propagated,
        'record_type': record_type,
        'record_id': str(row.pk),
        'enterprise_project': {
            'id': str(enterprise_project.pk),
            'code': enterprise_project.code,
            'name': enterprise_project.name,
        },
    }


@transaction.atomic
def save_project_relationship_exception(*, record_type, record_id, user, reason):
    """Record a reviewed exception without altering links, allocations or amounts."""
    from apps.finance.models import Invoice

    reason = _reason(reason, required=True)
    model = {
        'procurement_project': Project, 'purchase_requisition': PurchaseRequisition,
        'purchase_order': PurchaseOrder, 'invoice': Invoice,
    }.get(record_type) if isinstance(record_type, str) else None
    if model is None:
        raise ValidationError({'record_type': 'Unsupported record type.'})
    try:
        row = model.objects.select_for_update().get(pk=record_id)
    except (model.DoesNotExist, ValidationError, ValueError, TypeError) as exc:
        raise ValidationError({'record_id': 'Reconciliation record was not found.'}) from exc
    project_id = getattr(row, 'enterprise_project_id', None)
    audit = ProjectRelationshipResolution.objects.create(
        record_type=record_type, record_id=str(row.pk),
        previous_enterprise_project_id=project_id, enterprise_project_id=project_id,
        resolution='exception', reason=reason,
        resolved_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    return {
        'id': str(audit.pk), 'record_type': record_type, 'record_id': str(row.pk),
        'record_identifier': _identifier(record_type, row),
        'exception': {
            'reason': audit.reason, 'created_at': audit.created_at.isoformat(),
            'resolved_by': audit.resolved_by.get_full_name() if audit.resolved_by else None,
        },
    }
