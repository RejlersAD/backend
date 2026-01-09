"""
Finance Workflow Service
Main orchestration of invoice processing workflow
"""
from django.core.files.storage import default_storage
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from datetime import datetime
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
            
            # Get all Level 1 approvals for combined notification
            level_1_approvals = invoice.approvals.filter(approval_level=1)
            
            # Step 4: Send upload notification with approval button (for Level 1 approvers)
            try:
                uploaded_by = f"{invoice.submitted_by.get_full_name()} ({invoice.submitted_by.email})" if invoice.submitted_by else "Unknown User"
                # Pass first Level 1 approval (system will check each recipient individually)
                first_approval = level_1_approvals.first() if level_1_approvals.exists() else None
                self.email_service.send_invoice_upload_notification(invoice, uploaded_by, first_approval)
                logger.info(f"Upload notification sent for invoice {invoice.invoice_number}")
            except Exception as e:
                logger.warning(f"Failed to send upload notification for invoice {invoice.invoice_number}: {e}")
                # Don't fail the whole workflow if notification fails
            
            # Step 5: Send approval requests (skip Level 1 approvers who got upload notification)
            self._send_approval_requests(invoice, skip_level_1_in_notification=True)
            
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
            
            # Save vendor if found by regex
            vendor_name = invoice.vendor_name or extracted_data.get('vendor_name')
            invoice.vendor_name = vendor_name[:500] if vendor_name else None
            
            # Save invoice number if found by regex (or keep existing)
            invoice_number = invoice.invoice_number or extracted_data.get('invoice_number')
            invoice.invoice_number = invoice_number[:100] if invoice_number else None  # Let AI handle if None
            
            # Save amount and currency
            invoice.total_amount = invoice.total_amount or extracted_data.get('total_amount')
            invoice.currency = invoice.currency or extracted_data.get('currency', 'AED')
            invoice.save()
            
            logger.info(f"PDF extraction: invoice#={invoice.invoice_number}, vendor={invoice.vendor_name}, amount={invoice.total_amount}")
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
            
            # Log what AI returned
            logger.info(f"AI Classification Result: category={classification.get('category')}, "
                       f"vendor={classification.get('vendor_name')}, "
                       f"invoice#={classification.get('invoice_number')}, "
                       f"amount={classification.get('total_amount')}")
            
            # Update invoice with classification
            invoice.invoice_type = classification['category']
            invoice.classification_confidence = classification.get('confidence', 0.8)
            invoice.classification_reasoning = classification.get('reasoning', '')
            
            # ALWAYS use AI-extracted data (it's more accurate than PDF regex)
            ai_vendor = classification.get('vendor_name')
            ai_invoice_num = classification.get('invoice_number')
            ai_amount = classification.get('total_amount')
            
            # Update vendor if AI found a valid one
            if ai_vendor and ai_vendor not in ['UNKNOWN_VENDOR', 'NOT_FOUND', '']:
                logger.info(f"✓ AI extracted vendor: '{ai_vendor}'")
                invoice.vendor_name = ai_vendor[:500]
            
            # Update invoice number if AI found valid one (ignore autogenerated)
            if ai_invoice_num and ai_invoice_num not in ['NOT_FOUND', ''] and not ai_invoice_num.startswith('INV-GEN-'):
                logger.info(f"✓ AI extracted invoice#: '{ai_invoice_num}' (replacing '{invoice.invoice_number}')")
                
                # Check for duplicates and handle them
                base_invoice_num = ai_invoice_num[:100]
                final_invoice_num = base_invoice_num
                counter = 1
                
                while Invoice.objects.filter(invoice_number=final_invoice_num).exclude(id=invoice.id).exists():
                    logger.warning(f"⚠ Invoice# '{final_invoice_num}' already exists, appending suffix")
                    final_invoice_num = f"{base_invoice_num}-DUP{counter}"[:100]
                    counter += 1
                
                invoice.invoice_number = final_invoice_num
                if final_invoice_num != base_invoice_num:
                    logger.info(f"✓ Using unique invoice#: '{final_invoice_num}' (duplicate prevented)")
            else:
                logger.warning(f"⚠ AI did not find invoice number, keeping: '{invoice.invoice_number}'")
                # Generate unique invoice number ONLY if both PDF regex AND AI failed
                if not invoice.invoice_number or invoice.invoice_number in ['NOT_FOUND', '']:
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    invoice.invoice_number = f'INV-GEN-{timestamp}'
                    logger.info(f"Generated fallback invoice#: {invoice.invoice_number}")
            
            # Update amount if AI found valid one
            if ai_amount and ai_amount > 0:
                logger.info(f"✓ AI extracted amount: {ai_amount} (replacing {invoice.total_amount})")
                invoice.total_amount = ai_amount
            
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
            
            if not route.approval_chain:
                logger.warning(f"Approval route {route.id} has empty approval_chain")
                return False
            
            # Create approval records - SMART: Skip levels with empty/missing emails
            created_count = 0
            for level_config in route.approval_chain:
                # Skip if email is missing or empty
                email = level_config.get('email', '').strip()
                name = level_config.get('name', '').strip()
                
                if not email or not name:
                    logger.warning(f"Skipping approval level with missing data: name='{name}', email='{email}'")
                    continue
                
                Approval.objects.create(
                    invoice=invoice,
                    approver_name=name,
                    approver_email=email,
                    approval_level=level_config.get('level', 1),
                    level_name=name,
                    status=ApprovalStatus.PENDING,
                    approval_metadata={
                        'title': level_config.get('title', ''),
                        'cc': level_config.get('cc', []),
                        'mandatory': level_config.get('mandatory', False)
                    }
                )
                created_count += 1
            
            if created_count == 0:
                logger.warning(f"No valid approval levels created for invoice {invoice.invoice_number}")
                return False
            
            self._add_audit_log(invoice, "approval_workflow_created", f"Created {created_count} approval levels")
            logger.info(f"✓ Created {created_count} approval records for invoice {invoice.invoice_number}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create approval workflow: {e}", exc_info=True)
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
    
    def _send_approval_requests(self, invoice: Invoice, skip_level_1_in_notification: bool = False):
        """
        Send approval request to first level approvers
        skip_level_1_in_notification: If True, skip Level 1 entirely (they already got upload notification with approval button)
        """
        try:
            # Get all level 1 approvals
            approvals = invoice.approvals.filter(
                approval_level=1,
                status=ApprovalStatus.PENDING
            )
            
            # If skip flag is True, don't send separate approval emails to Level 1
            # (they already got combined upload notification with approval button)
            if skip_level_1_in_notification:
                logger.info(f"Skipping separate Level 1 approval emails - already included in upload notification")
                return
            
            for approval in approvals:
                self.email_service.send_approval_request(approval, invoice)
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
    
    def process_approval_decision(self, approval_token: str, decision: str, comments: str = '') -> bool:
        """Process approval or rejection decision"""
        try:
            logger.info(f"🔄 Processing approval decision: token={approval_token}, decision={decision}")
            
            approval = Approval.objects.get(approval_token=approval_token, status=ApprovalStatus.PENDING)
            invoice = approval.invoice
            
            logger.info(f"📋 Found approval: {approval.approver_name} (Level {approval.approval_level}) for invoice {invoice.invoice_number}")
            
            approval.status = ApprovalStatus.APPROVED if decision == 'approve' else ApprovalStatus.REJECTED
            approval.decision = decision
            approval.comments = comments
            approval.decision_date = timezone.now()
            approval.save()
            
            self._add_audit_log(
                invoice,
                f"approval_{decision}",
                f"{approval.approver_name} {decision}d at level {approval.approval_level}"
            )
            
            logger.info(f"✅ {approval.approver_name} {decision}d invoice {invoice.invoice_number} (Level {approval.approval_level})")
            
            # Send notification
            try:
                self.email_service.send_approval_notification(invoice, approval.approver_name, decision)
                logger.info(f"📧 Approval notification sent for {decision}")
            except Exception as notif_error:
                logger.error(f"❌ Failed to send approval notification: {notif_error}")
            
            # If rejected, mark invoice as rejected and notify vendor
            if decision == 'reject':
                invoice.status = InvoiceStatus.REJECTED
                invoice.save()
                logger.info(f"✗ Invoice {invoice.invoice_number} rejected at level {approval.approval_level}")
                
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
            
            logger.info(f"📊 Level {current_level} status check: {level_approvals.count()} approvers")
            for la in level_approvals:
                logger.info(f"   - {la.approver_name}: {la.status}")
            
            if all(a.status == ApprovalStatus.APPROVED for a in level_approvals):
                logger.info(f"✅ All Level {current_level} approvals complete!")
                
                # Move to next level or mark as approved
                next_level_exists = invoice.approvals.filter(approval_level=current_level + 1).exists()
                
                if next_level_exists:
                    # Send next level approval requests
                    next_approvals = invoice.approvals.filter(
                        approval_level=current_level + 1,
                        status=ApprovalStatus.PENDING
                    )
                    
                    logger.info(f"📧 Sending emails to {next_approvals.count()} Level {current_level + 1} approvers:")
                    
                    # Get next level info for confirmation email
                    next_level_names = [na.approver_name for na in next_approvals]
                    next_level_info = ", ".join(next_level_names) if next_level_names else "Next Level Approvers"
                    
                    # Send confirmation email to current approver
                    try:
                        self.email_service.send_approval_confirmation(
                            invoice, 
                            approval.approver_email, 
                            approval.approver_name,
                            next_level_info
                        )
                        logger.info(f"✅ Confirmation sent to {approval.approver_email}")
                    except Exception as conf_error:
                        logger.error(f"❌ Failed to send confirmation to {approval.approver_email}: {conf_error}")
                    
                    # Send approval requests to next level
                    for next_approval in next_approvals:
                        try:
                            logger.info(f"📤 Sending to {next_approval.approver_email} ({next_approval.approver_name})...")
                            result = self.email_service.send_approval_request(next_approval, invoice)
                            if result:
                                logger.info(f"✅ Email sent successfully to {next_approval.approver_email}")
                            else:
                                logger.error(f"❌ Email send returned False for {next_approval.approver_email}")
                        except Exception as email_error:
                            logger.error(f"❌ Failed to send email to {next_approval.approver_email}: {email_error}", exc_info=True)
                else:
                    # All approvals complete - this was the final approval
                    invoice.status = InvoiceStatus.APPROVED
                    invoice.processed_at = timezone.now()
                    invoice.save()
                    self._add_audit_log(invoice, "fully_approved", "All approvals completed")
                    logger.info(f"✅✅ Invoice {invoice.invoice_number} FULLY APPROVED - All levels complete")
                    
                    # Send final confirmation email to last approver
                    try:
                        self.email_service.send_approval_confirmation(
                            invoice, 
                            approval.approver_email, 
                            approval.approver_name,
                            None  # No next level - this was final
                        )
                        logger.info(f"📧 Final confirmation sent to {approval.approver_email}")
                    except Exception as final_conf_error:
                        logger.error(f"❌ Failed to send final confirmation: {final_conf_error}")
            else:
                # Not all approvals at this level are complete yet
                pending_count = level_approvals.filter(status=ApprovalStatus.PENDING).count()
                logger.info(f"⏳ Level {current_level} not complete yet - {pending_count} approvals still pending")
                for la in level_approvals.filter(status=ApprovalStatus.PENDING):
                    logger.info(f"   - Still waiting for: {la.approver_name} ({la.approver_email})")
            
            return True
            
        except Approval.DoesNotExist:
            logger.error(f"Approval token not found: {approval_token}")
            return False
        except Exception as e:
            logger.error(f"Approval processing failed: {e}", exc_info=True)
            return False
