"""Atomic Purchase Requisition to Purchase Order conversion."""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from ..models import PurchaseOrder, PurchaseRequisition, Vendor
from .requisition_status import canonicalize_pr_status


class RequisitionConversionService:
    """Create exactly one draft PO from an approved requisition."""

    @classmethod
    def _po_number(cls, pr):
        """Derive a stable PO number from the unique PR number."""
        pr_number = str(pr.pr_number or pr.id)
        if '-PR-' in pr_number:
            po_number = pr_number.replace('-PR-', '-PUR-', 1)
        else:
            po_number = f'PO-{pr_number}'
        return po_number[:50]

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
        workflow = pr.approval_workflow_config if isinstance(pr.approval_workflow_config, list) else []
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
            pr_requester_name=requester_name,
            vendor=vendor,
            seller_reference=vendor.contact_person or '',
            seller_license_no=pr.supplier_business_id or vendor.trade_license_number or '',
            seller_contact_person=vendor.contact_person or '',
            seller_phone=vendor.phone or '',
            seller_email=vendor.email or '',
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

        # The surrounding transaction rolls back both operations on failure.
        pr.status = 'converted'
        pr.po_number_reference = po.po_number
        update_fields = ['status', 'po_number_reference', 'updated_at']
        if vendor_was_linked:
            update_fields.append('vendor')
        pr.save(update_fields=update_fields)
        return pr, po
