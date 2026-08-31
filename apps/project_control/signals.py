"""Persisted-fact listeners connecting Procurement and Finance to Project Control."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.finance.models import Invoice, InvoiceMatchStatus, InvoicePurchaseOrderAllocation, PayablePayment
from apps.procurement.models import PurchaseOrder, Receipt

from .services.commercial_events import projects_for_invoice, record_commercial_event, safe_after_commit


@receiver(post_save, sender=PurchaseOrder, dispatch_uid='pc_commercial_po')
def purchase_order_event(sender, instance, **kwargs):
    if not instance.approved_at:
        return
    safe_after_commit(lambda: record_commercial_event(
        project=instance.enterprise_project, event_key=f'po:{instance.pk}:approved', event_type='po_approved',
        source_type='purchase_order', source_id=instance.pk, source_reference=instance.po_number,
        amount=instance.total_amount, currency=instance.currency, event_at=instance.approved_at, actor=instance.approved_by,
    ))


@receiver(post_save, sender=Receipt, dispatch_uid='pc_commercial_receipt')
def receipt_event(sender, instance, **kwargs):
    if instance.status not in ('accepted', 'partial'):
        return
    safe_after_commit(lambda: record_commercial_event(
        project=instance.purchase_order.enterprise_project,
        event_key=f'receipt:{instance.pk}:{instance.status}', event_type='receipt_accepted',
        source_type='receipt', source_id=instance.pk, source_reference=instance.receipt_number,
        event_at=instance.updated_at, actor=instance.received_by, payload={'status': instance.status},
    ))


@receiver(post_save, sender=InvoicePurchaseOrderAllocation, dispatch_uid='pc_commercial_invoice_match')
def invoice_match_event(sender, instance, **kwargs):
    if instance.match_status != InvoiceMatchStatus.VERIFIED:
        return
    safe_after_commit(lambda: record_commercial_event(
        project=instance.purchase_order.enterprise_project,
        event_key=f'invoice-allocation:{instance.pk}:verified', event_type='invoice_verified',
        source_type='invoice_allocation', source_id=instance.pk, source_reference=instance.invoice.invoice_number,
        amount=instance.allocated_amount, currency=instance.currency,
        event_at=instance.verified_at or instance.updated_at, actor=instance.verified_by,
    ))


@receiver(post_save, sender=Invoice, dispatch_uid='pc_commercial_invoice')
def invoice_event(sender, instance, **kwargs):
    if instance.status not in ('approved', 'processed') and instance.procurement_status not in ('approved_for_payment', 'closed'):
        return
    def capture():
        for project in projects_for_invoice(instance):
            record_commercial_event(
                project=project, event_key=f'invoice:{instance.pk}:approved:{project.pk}', event_type='invoice_approved',
                source_type='invoice', source_id=instance.pk, source_reference=instance.invoice_number,
                amount=instance.total_amount, currency=instance.currency, event_at=instance.updated_at,
            )
    safe_after_commit(capture)


@receiver(post_save, sender=PayablePayment, dispatch_uid='pc_commercial_payment')
def payment_event(sender, instance, **kwargs):
    event_type = {'schedule': 'payment_scheduled', 'payment': 'payment_recorded', 'hold': 'payment_held',
                  'release': 'payment_released', 'cancel': 'payment_cancelled'}[instance.operation]
    def capture():
        for project in projects_for_invoice(instance.invoice):
            record_commercial_event(
                project=project, event_key=f'payable:{instance.pk}:{project.pk}', event_type=event_type,
                source_type='payable_payment', source_id=instance.pk,
                source_reference=instance.reference or instance.invoice.invoice_number,
                amount=instance.amount, currency=instance.currency, event_at=instance.created_at,
                actor=instance.created_by, payload={'invoice_id': instance.invoice_id, 'operation': instance.operation},
            )
    safe_after_commit(capture)
