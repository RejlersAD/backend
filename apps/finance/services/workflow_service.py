"""
Finance Workflow Service
Main orchestration of invoice processing workflow
"""
from django.core.files.storage import default_storage
from django.utils import timezone
from django.db import transaction
from .pdf_extractor import PDFExtractor
from .ai_classifier import InvoiceClassifier
from .email_service import EmailService
from ..models import Invoice, Approval, AuditLog, ApprovalRoute, InvoiceStatus, ApprovalStatus
import logging
import os

logger = logging.getLogger(__name__)


class FinanceWorkflowService:
    """Main workflow orchestration for invoice processing"""
    
    def __init__(self):
        self.pdf_extractor = PDFExtractor()
        self.ai_classifier = InvoiceClassifier()
        self.email_service = EmailService()
    
    def process_invoice(self, invoice_id: int) -> bool:
        """
        Complete invoice processing workflow
        Returns True if successful, False otherwise
        """
        try:
            invoice = Invoice.objects.get(id=invoice_id)
            self._add_audit_log(invoice, "workflow_started", "Invoice processing workflow initiated")
            
            # Step 1: Extract PDF text and data
            if not self._extract_invoice_data(invoice):
                logger.error(f"Failed to extract data for invoice {invoice_id}")
                return False
            
            # Reload invoice to get latest data
            invoice.refresh_from_db()
            
            # Step 2: AI Classification
            if not self._classify_invoice(invoice):
                logger.error(f"Failed to classify invoice {invoice_id}")
                return False
            
            # Reload invoice to get latest data
            invoice.refresh_from_db()
            
            # Step 3: Create approval workflow
            if not self._create_approval_workflow(invoice):
                logger.error(f"Failed to create approval workflow for invoice {invoice_id}")
                return False
            
            # Step 4: Send first approval request
            self._send_approval_requests(invoice)
            
            invoice.status = InvoiceStatus.PENDING_APPROVAL
            invoice.save()
            
            self._add_audit_log(invoice, "workflow_completed", "Invoice ready for approval")
            logger.info(f"Invoice {invoice.invoice_number} processed successfully")
            
            return True
            
        except Invoice.DoesNotExist:
            logger.error(f"Invoice {invoice_id} not found")
            return False
        except Exception as e:
            logger.error(f"Workflow failed for invoice {invoice_id}: {e}")
            return False
    
    def _extract_invoice_data(self, invoice: Invoice) -> bool:
        """Extract data from PDF"""
        try:
            file_path = invoice.file_path
            if not os.path.exists(file_path):
                invoice.status = InvoiceStatus.EXTRACTION_FAILED
                invoice.save()
                self._add_audit_log(invoice, "extraction_failed", "File not found")
                return False
            
            # Extract invoice data
            extracted_data = self.pdf_extractor.process_invoice(file_path)
            
            if not extracted_data:
                invoice.status = InvoiceStatus.EXTRACTION_FAILED
                invoice.save()
                self._add_audit_log(invoice, "extraction_failed", "PDF extraction failed")
                self.email_service.send_error_notification(
                    invoice.invoice_number,
                    "Extraction Failed",
                    "Could not extract text from PDF"
                )
                return False
            
            # Update invoice with extracted data (truncate to field limits)
            invoice.extracted_text = extracted_data.get('extracted_text', '')
            vendor_name = invoice.vendor_name or extracted_data.get('vendor_name')
            invoice.vendor_name = vendor_name[:500] if vendor_name else None  # Truncate to 500 chars
            invoice_number = invoice.invoice_number or extracted_data.get('invoice_number')
            invoice.invoice_number = invoice_number[:100] if invoice_number else invoice.invoice_number  # Truncate to 100
            invoice.total_amount = invoice.total_amount or extracted_data.get('total_amount')
            invoice.currency = invoice.currency or extracted_data.get('currency', 'AED')
            invoice.save()
            
            self._add_audit_log(invoice, "extraction_success", f"Extracted {len(invoice.extracted_text)} characters")
            return True
            
        except Exception as e:
            logger.error(f"Extraction failed: {e}", exc_info=True)
            try:
                invoice.status = InvoiceStatus.EXTRACTION_FAILED
                invoice.save()
            except Exception as save_error:
                logger.error(f"Failed to save extraction error status: {save_error}")
            return False
    
    def _classify_invoice(self, invoice: Invoice) -> bool:
        """Classify invoice using AI"""
        try:
            classification = self.ai_classifier.classify_invoice(
                invoice.extracted_text,
                {
                    'vendor_name': invoice.vendor_name,
                    'invoice_number': invoice.invoice_number,
                    'total_amount': float(invoice.total_amount) if invoice.total_amount else None
                }
            )
            
            if not classification or not classification.get('category'):
                invoice.status = InvoiceStatus.CLASSIFICATION_FAILED
                invoice.save()
                self._add_audit_log(invoice, "classification_failed", "AI classification failed")
                self.email_service.send_error_notification(
                    invoice.invoice_number,
                    "Classification Failed",
                    "Could not classify invoice"
                )
                return False
            
            # Update invoice with classification
            invoice.invoice_type = classification['category']
            invoice.classification_confidence = classification.get('confidence', 0.8)
            invoice.classification_reasoning = classification.get('reasoning', '')
            
            # Use AI-extracted data if better than PDF extraction
            if classification.get('vendor_name'):
                invoice.vendor_name = classification['vendor_name'][:500]  # Truncate to field limit
            if classification.get('invoice_number'):
                invoice.invoice_number = classification['invoice_number'][:100]  # Truncate to field limit
            if classification.get('total_amount'):
                invoice.total_amount = classification['total_amount']
            
            invoice.save()
            
            self._add_audit_log(
                invoice,
                "classification_success",
                f"Classified as {invoice.invoice_type} (confidence: {invoice.classification_confidence})"
            )
            return True
            
        except Exception as e:
            logger.error(f"Classification failed: {e}", exc_info=True)
            try:
                invoice.status = InvoiceStatus.CLASSIFICATION_FAILED
                invoice.save()
            except Exception as save_error:
                logger.error(f"Failed to save classification error status: {save_error}")
            return False
    
    def _upload_to_drive(self, invoice: Invoice):
        """Upload invoice to Google Drive - DISABLED"""
        # Google Drive integration disabled - invoices stored in database
        pass
    
    def _create_approval_workflow(self, invoice: Invoice) -> bool:
        """Create approval workflow based on invoice type and amount"""
        try:
            route = self._get_approval_route(invoice)
            
            if not route:
                logger.warning(f"No approval route found for {invoice.invoice_type}")
                return False
            
            # Create approval records
            for level_config in route.approval_chain:
                Approval.objects.create(
                    invoice=invoice,
                    approver_name=level_config['name'],
                    approver_email=level_config['email'],
                    approval_level=level_config['level'],
                    level_name=level_config.get('name', ''),
                    status=ApprovalStatus.PENDING,
                    approval_metadata={
                        'title': level_config.get('title', ''),
                        'cc': level_config.get('cc', []),
                        'mandatory': level_config.get('mandatory', False)
                    }
                )
            
            self._add_audit_log(invoice, "approval_workflow_created", f"Created {len(route.approval_chain)} approval levels")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create approval workflow: {e}")
            return False
    
    def _get_approval_route(self, invoice: Invoice):
        """Get applicable approval route for invoice"""
        routes = ApprovalRoute.objects.filter(
            invoice_type=invoice.invoice_type,
            is_active=True
        ).order_by('-priority')
        
        for route in routes:
            # Check amount constraints if specified
            if route.min_amount and invoice.total_amount < route.min_amount:
                continue
            if route.max_amount and invoice.total_amount > route.max_amount:
                continue
            return route
        
        return None
    
    def _send_approval_requests(self, invoice: Invoice):
        """Send approval request to first level approvers"""
        try:
            # Get all level 1 approvals
            approvals = invoice.approvals.filter(
                approval_level=1,
                status=ApprovalStatus.PENDING
            )
            
            for approval in approvals:
                self.email_service.send_approval_request(invoice, approval)
                self._add_audit_log(invoice, "approval_email_sent", f"Sent to {approval.approver_email}")
            
        except Exception as e:
            logger.error(f"Failed to send approval requests: {e}")
    
    def _log_to_sheets(self, invoice: Invoice):
        """Log invoice to Google Sheets - DISABLED"""
        # Google Sheets logging disabled - use database audit logs instead
        pass
    
    def _add_audit_log(self, invoice: Invoice, action: str, description: str):
        """Add audit log entry"""
        AuditLog.objects.create(
            invoice=invoice,
            action=action,
            description=description,
            user=None  # System action
        )
    
    def process_approval_decision(self, approval_token: str, decision: str) -> bool:
        """Process approval or rejection decision"""
        try:
            approval = Approval.objects.get(approval_token=approval_token, status=ApprovalStatus.PENDING)
            invoice = approval.invoice
            
            approval.status = ApprovalStatus.APPROVED if decision == 'approve' else ApprovalStatus.REJECTED
            approval.save()
            
            self._add_audit_log(
                invoice,
                f"approval_{decision}",
                f"{approval.approver_name} {decision}d at level {approval.approval_level}"
            )
            
            # Send notification
            self.email_service.send_approval_notification(invoice, approval.approver_name, decision)
            
            # If rejected, mark invoice as rejected and notify vendor
            if decision == 'reject':
                invoice.status = InvoiceStatus.REJECTED
                invoice.save()
                
                # Send rejection email to vendor
                rejection_reason = f"Your invoice has been rejected during the approval process at level {approval.approval_level}."
                self.email_service.send_rejection_to_vendor(
                    invoice=invoice,
                    rejection_reason=rejection_reason,
                    rejected_by=approval.approver_name
                )
                
                return True
            
            # If approved, check if all approvals at this level are complete
            current_level = approval.approval_level
            level_approvals = invoice.approvals.filter(approval_level=current_level)
            
            if all(a.status == ApprovalStatus.APPROVED for a in level_approvals):
                # Move to next level or mark as approved
                next_level_exists = invoice.approvals.filter(approval_level=current_level + 1).exists()
                
                if next_level_exists:
                    # Send next level approval requests
                    next_approvals = invoice.approvals.filter(
                        approval_level=current_level + 1,
                        status=ApprovalStatus.PENDING
                    )
                    for next_approval in next_approvals:
                        self.email_service.send_approval_request(invoice, next_approval)
                else:
                    # All approvals complete
                    invoice.status = InvoiceStatus.APPROVED
                    invoice.processed_at = timezone.now()
                    invoice.save()
                    self._add_audit_log(invoice, "fully_approved", "All approvals completed")
            
            return True
            
        except Approval.DoesNotExist:
            logger.error(f"Approval token not found: {approval_token}")
            return False
        except Exception as e:
            logger.error(f"Approval processing failed: {e}")
            return False
