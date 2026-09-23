"""Atomic Purchase Requisition to Purchase Order conversion."""

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from ..models import PurchaseOrder, PurchaseRequisition, Vendor
from .purchase_order_numbering import PurchaseOrderNumberService
from .requisition_status import canonicalize_pr_status
from .employee_display import normalize_ceo_workflow
from .pr_document_reconciliation import compare_existing_pr
from .purchase_order_project_display import requisition_project_reference
from .purchase_order_approvals import (
    default_management_assignment, notify_assigned_approvers, notify_purchase_order_created,
)


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

    @staticmethod
    def _supplier_identity(value):
        # Ignore presentation punctuation/case, but keep the actual company
        # name and legal suffix; fuzzy name similarity cannot authorize a PO.
        normalized = unicodedata.normalize('NFKC', str(value or '')).casefold().replace('&', 'and')
        return re.sub(r'[^\w]', '', normalized, flags=re.UNICODE)

    @classmethod
    def _signed_document_snapshot(cls, pr):
        verification = (getattr(pr, 'price_remarks_data', None) or {}).get('signed_document_verification') or {}
        if not verification.get('signed_off') and not verification.get('attach_only'):
            return None
        snapshot = verification.get('approved_fields') or verification.get('source_fields')
        if not snapshot:
            raise ValidationError({'error': 'Review the approved values from the signed PDF before creating a purchase order.'})
        compared = dict(snapshot)
        if verification.get('approved_fields') and not verification.get('attach_only'):
            # The creation/update import recorded the reviewer's corrections as
            # its approved snapshot. Raw attachment identity remains stricter.
            compared['field_confidence'] = {**compared.get('field_confidence', {}), 'pr_number': 'reviewed'}
            compared['field_provenance'] = {**compared.get('field_provenance', {}),
                                           'pr_number': {'source': 'approved_document_review'}}
        comparison = compare_existing_pr(pr, compared)
        differences = [field['label'] for field in comparison['fields'] if field['status'] == 'mismatch']
        if not comparison['identity_matched']:
            differences.append('PR number')
        try:
            source_total = Decimal(str(snapshot.get('net_total', '')).replace(',', ''))
            current_total = cls._total_amount(pr)
            if getattr(pr, 'vat_basis', 'unconfirmed') in {'exclusive', 'inclusive', 'none'} and pr.net_total_excl_vat is not None:
                current_total = pr.net_total_excl_vat
            money_matches = (source_total.is_finite() and current_total.is_finite()
                             and source_total.quantize(Decimal('0.01')) == current_total.quantize(Decimal('0.01')))
        except (InvalidOperation, ValueError, TypeError):
            money_matches = False
        if not money_matches:
            differences.append('Purchase order amount')
        if not snapshot.get('currency'):
            differences.append('PDF currency')
        elif str(getattr(pr, 'currency', '') or 'USD').strip().upper() != str(snapshot['currency']).strip().upper():
            differences.append('Currency')
        if differences:
            raise ValidationError({'error': 'Resolve the differences between this recommendation and its signed PDF before creating a purchase order: '
                                            + ', '.join(dict.fromkeys(differences)) + '.'})
        return snapshot

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
            getattr(pr, 'po_applicable', None),
        )
        return [
            {
                'stage': stage.get('stage') or stage.get('role') or f"Stage {index + 1}",
                'external': True,
                'source': 'purchase_requisition',
                'source_pr_id': str(pr.pk),
                'approver': stage.get('approved_by_name') or stage.get('user_name') or '',
                'source_status': stage.get('status', 'pending'),
                'status': ('Approved' if str(stage.get('status', '')).strip().lower()
                           in {'approved', 'complete', 'completed'} else str(stage.get('status', 'pending')).title()),
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
        signed_snapshot = cls._signed_document_snapshot(pr)
        workflow = normalize_ceo_workflow(
            pr.approval_workflow_config,
            pr.po_number_reference,
            getattr(pr, 'po_applicable', None),
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
        if signed_snapshot:
            source_supplier = signed_snapshot.get('supplier_name') or signed_snapshot.get('preferred_supplier')
            if not cls._supplier_identity(source_supplier) or cls._supplier_identity(vendor.name) != cls._supplier_identity(source_supplier):
                raise ValidationError({'error': 'The selected vendor does not match the supplier on the signed PDF. Select the correct vendor before creating a purchase order.'})

        total_amount = cls._total_amount(pr)
        po_number = cls._po_number(pr)
        if PurchaseOrder.objects.filter(po_number=po_number).exists():
            raise ValidationError({'error': f'Purchase order number {po_number} is already in use.'})

        # Conversion starts the PO's own approval request. A source PR's
        # completed history is evidence of that PR, never a PO decision.
        po_assignments = default_management_assignment()

        requester_name = ''
        if pr.issued_by:
            requester_name = pr.issued_by.get_full_name() or pr.issued_by.email

        pricing_data = pr.price_remarks_data if isinstance(pr.price_remarks_data, dict) else {}
        project_reference = requisition_project_reference(pr)
        if len(project_reference) > PurchaseOrder._meta.get_field('project_number').max_length:
            raise ValidationError({'project_number': 'The combined project numbers exceed 100 characters. Shorten the project references before conversion.'})
        confirmed_financials = {}
        item_amount = total_amount
        if getattr(pr, 'vat_basis', 'unconfirmed') in {'exclusive', 'inclusive', 'none'} and pr.net_total_excl_vat is not None:
            confirmed_financials = {
                'vat_basis': pr.vat_basis, 'net_amount': pr.net_total_excl_vat,
                'tax_amount': total_amount - pr.net_total_excl_vat,
                'vat_percentage': Decimal('0') if pr.vat_basis == 'none' else Decimal('5'),
                'discount_amount': Decimal(str(pricing_data.get('discount_amount') or 0)),
            }
            item_amount = (total_amount if pr.vat_basis == 'inclusive' else pr.net_total_excl_vat) + confirmed_financials['discount_amount']
        po = PurchaseOrder.objects.create(
            **confirmed_financials,
            po_number=po_number,
            pr_reference=pr,
            enterprise_project=getattr(pr, 'enterprise_project', None),
            pr_requester_name=requester_name,
            vendor=vendor,
            seller_reference=vendor.contact_person or '',
            seller_license_no=pr.supplier_business_id or vendor.trade_license_number or '',
            seller_contact_person='',
            seller_phone=vendor.phone or '',
            seller_email=vendor.email or '',
            seller_address=vendor.address or '',
            title=pr.product_service or pr.title or f'Purchase Order for {pr.pr_number}',
            description=pr.description_reason or '',
            category=pr.category or 'other',
            total_amount=total_amount,
            currency=pr.currency or 'USD',
            payment_terms=str(pricing_data.get('payment_terms') or ''),
            project_number=project_reference,
            project_manager=pr.pm_name.get_full_name() if pr.pm_name else '',
            budget=pr.estimated_budget,
            items=cls._items(pr, item_amount),
            expected_delivery=pr.required_date,
            scope_of_services=pr.description_reason or '',
            approval_log=[*cls._approval_log(pr), *po_assignments],
            management_approver=po_assignments[-1]['approver'],
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
        from .procurement_lifecycle import PREVIOUS_STATUS
        pr.price_remarks_data = dict(pr.price_remarks_data or {})
        pr.price_remarks_data[PREVIOUS_STATUS] = canonicalize_pr_status(pr.status)
        pr.status = 'converted'
        pr.po_number_reference = po.po_number
        update_fields = ['status', 'po_number_reference', 'price_remarks_data', 'updated_at']
        if vendor_was_linked:
            update_fields.append('vendor')
        pr.save(update_fields=update_fields)
        transaction.on_commit(lambda: notify_assigned_approvers(po), robust=True)
        transaction.on_commit(lambda: notify_purchase_order_created(po), robust=True)
        return pr, po
