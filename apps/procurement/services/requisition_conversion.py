"""Atomic Purchase Requisition to Purchase Order conversion."""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from ..models import PurchaseOrder, PurchaseRequisition, Vendor
from .purchase_order_numbering import PurchaseOrderNumberService
from .requisition_status import canonicalize_pr_status
from .employee_display import normalize_ceo_workflow


class RequisitionConversionService:
    """Create exactly one draft PO from an approved requisition."""

    @classmethod
    def _po_number(cls, pr):
        """Derive a stable PO number from the unique PR number."""
        try:
            return PurchaseOrderNumberService.from_requisition(pr.pr_number)
        except ValueError as exc:
            raise ValidationError({'error': str(exc)}) from exc

    @classmethod
    def _total_amount(cls, pr):
        raw_amount = pr.total_price if pr.total_price is not None else pr.estimated_budget
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            amount = Decimal('0')
        if amount <= 0:
            raise ValidationError({'error': 'A positive requisition total is required before conversion.'})
        return amount

    @classmethod
    def _items(cls, pr, total_amount):
        if isinstance(pr.items, list) and pr.items:
            return pr.items
        return [{
            'item': pr.price_description or pr.product_service or pr.title or 'Requisition item',
            'quantity': 1,
            'unit': 'lot',
            'unit_price': float(total_amount),
            'total': float(total_amount),
        }]

    @classmethod
    def _approval_log(cls, pr):
        workflow = normalize_ceo_workflow(
            pr.approval_workflow_config,
            pr.po_number_reference,
        )
        return [
            {
                'stage': stage.get('stage') or stage.get('role') or f"Stage {index + 1}",
                'approver': stage.get('approved_by_name') or stage.get('user_name') or '',
                'status': str(stage.get('status', 'pending')).title(),
                'date': stage.get('approved_at') or '',
                'comments': 'Approved on source purchase requisition.',
            }
            for index, stage in enumerate(workflow)
        ]

    @classmethod
    def _resolve_vendor(cls, pr):
        """Use the linked vendor or safely match one exact active master record."""
        if pr.vendor_id and pr.vendor:
            return pr.vendor, False

        lookup = Q()
        supplier_names = {
            str(name).strip()
            for name in (pr.supplier_name, pr.preferred_supplier_if_any)
            if str(name or '').strip()
        }
        for supplier_name in supplier_names:
            lookup |= Q(name__iexact=supplier_name)
        if str(pr.supplier_business_id or '').strip():
            lookup |= Q(trade_license_number__iexact=str(pr.supplier_business_id).strip())

        candidates = list(Vendor.objects.filter(lookup, status='active')[:2]) if lookup else []
        if len(candidates) == 1:
            pr.vendor = candidates[0]
            return candidates[0], True
        if len(candidates) > 1:
            raise ValidationError({
                'error': 'Multiple active vendors match this supplier. Link the intended vendor before conversion.'
            })
        raise ValidationError({
            'error': 'A linked vendor is required before conversion; no exact active vendor match was found.'
        })

    @classmethod
    @transaction.atomic
    def convert(cls, pr_id, actor):
        pr = get_object_or_404(
            # Do not join nullable relations here: PostgreSQL cannot apply
            # FOR UPDATE to the nullable side of an outer join. Lazy relation
            # loads remain inside this transaction while only the PR row is
            # locked for duplicate-conversion protection.
            PurchaseRequisition.objects.select_for_update(),
            pk=pr_id,
        )
        return cls._convert_locked(pr, actor)

    @classmethod
    def _convert_locked(cls, pr, actor):
        existing_po = pr.purchase_orders.order_by('created_at').first()
        if existing_po:
            raise ValidationError({
                'error': f'This requisition was already converted to {existing_po.po_number}.',
                'purchase_order_id': str(existing_po.id),
            })
        current_status = canonicalize_pr_status(pr.status)
        if current_status == 'converted':
            raise ValidationError({'error': 'This requisition has already been converted.'})
        if current_status != 'approved':
            raise ValidationError({'error': 'Only approved requisitions can be converted to a purchase order.'})
        workflow = normalize_ceo_workflow(
            pr.approval_workflow_config,
            pr.po_number_reference,
        )
        unresolved_stages = [
            stage.get('role') or stage.get('stage') or f'Stage {index + 1}'
            for index, stage in enumerate(workflow)
            if isinstance(stage, dict)
            and str(stage.get('status', 'pending')).strip().lower()
            not in {'approved', 'complete', 'completed'}
        ]
        if unresolved_stages:
            raise ValidationError({
                'error': (
                    'All configured approval stages must be approved before conversion. '
                    f"Unresolved: {', '.join(unresolved_stages)}."
                ),
            })
        vendor, vendor_was_linked = cls._resolve_vendor(pr)
        if vendor.status != 'active':
            raise ValidationError({'error': 'The linked vendor must be active before conversion.'})

        total_amount = cls._total_amount(pr)
        po_number = cls._po_number(pr)
        if PurchaseOrder.objects.filter(po_number=po_number).exists():
            raise ValidationError({'error': f'Purchase order number {po_number} is already in use.'})

        requester_name = ''
        if pr.issued_by:
            requester_name = pr.issued_by.get_full_name() or pr.issued_by.email

        pricing_data = pr.price_remarks_data if isinstance(pr.price_remarks_data, dict) else {}
        po = PurchaseOrder.objects.create(
            po_number=po_number,
            pr_reference=pr,
            enterprise_project=pr.enterprise_project,
            pr_requester_name=requester_name,
            vendor=vendor,
            seller_reference=vendor.contact_person or '',
            seller_license_no=pr.supplier_business_id or vendor.trade_license_number or '',
            seller_contact_person=vendor.contact_person or '',
            seller_phone=vendor.phone or '',
            seller_email=vendor.email or '',
            seller_address=vendor.address or '',
            title=pr.product_service or pr.title or f'Purchase Order for {pr.pr_number}',
            description=pr.description_reason or '',
            category=pr.category or 'other',
            total_amount=total_amount,
            currency=pr.currency or 'USD',
            payment_terms=str(pricing_data.get('payment_terms') or ''),
            project_number=pr.project or '',
            project_manager=pr.pm_name.get_full_name() if pr.pm_name else '',
            budget=pr.estimated_budget,
            items=cls._items(pr, total_amount),
            expected_delivery=pr.required_date,
            scope_of_services=pr.description_reason or '',
            approval_log=cls._approval_log(pr),
            final_approver_notes=pr.purchase_recommendation or '',
            created_by=actor,
            notes=pr.notes or '',
            attachments=list(pr.attachments or []),
        )

        # Preserve approved/draft multi-project WBS splits as draft PO
        # allocations. Procurement approval does not silently post a financial
        # commitment; Project Control must approve the carried allocations.
        from apps.project_control.models import CostAllocation
        for allocation in CostAllocation.objects.filter(
            source_type='purchase_requisition', source_id=str(pr.pk),
            is_deleted=False,
        ):
            CostAllocation.objects.create(
                project=allocation.project,
                wbs_node=allocation.wbs_node,
                budget_allocation=allocation.budget_allocation,
                source_type='purchase_order',
                source_id=str(po.pk),
                source_reference=po.po_number,
                amount=allocation.amount,
                currency=po.currency,
                status='draft',
                notes=f'Carried from {pr.pr_number}; requires Project Control approval.',
                allocated_by=actor,
            )

        # The surrounding transaction rolls back both operations on failure.
        pr.status = 'converted'
        pr.po_number_reference = po.po_number
        update_fields = ['status', 'po_number_reference', 'updated_at']
        if vendor_was_linked:
            update_fields.append('vendor')
        pr.save(update_fields=update_fields)
        return pr, po
