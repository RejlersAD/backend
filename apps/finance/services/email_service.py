"""
Email Service for Finance Module
Sends notifications and approval emails
"""
from django.core.mail import send_mail, EmailMultiAlternatives, EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
import logging
import os

logger = logging.getLogger(__name__)


class EmailService:
    """Handle all email notifications for finance workflow"""
    
    def __init__(self):
        self.from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@radai.com')
    
    def send_approval_request(self, approval, invoice):
        """Send approval request email with approve/reject buttons and PDF attachment"""
        try:
            # Parse approval metadata for CC and title
            metadata = approval.approval_metadata or {}
            cc_emails = metadata.get('cc', [])
            title = metadata.get('title', '')
            mandatory = metadata.get('mandatory', False)
            
            # Build approval URLs
            base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            # Frontend URL for approval page
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            approval_page_url = f"{frontend_url}/finance/approve/{approval.approval_token}"
            
            # Build level text
            level_text = f"Level {approval.approval_level}"
            if title:
                level_text += f" - {title}"
            if mandatory or 'CEO' in title.upper():
                level_text += " (MANDATORY)"
            
            subject = f"Invoice Approval Required ({level_text}) - {invoice.invoice_number}"
            
            # Safe display values
            try:
                amount_display = f"{invoice.total_amount:,.2f}" if invoice.total_amount else "0.00"
            except:
                amount_display = str(invoice.total_amount or "0.00")
            
            vendor_display = invoice.vendor_name or "N/A"
            currency_display = invoice.currency or "AED"
            type_display = invoice.get_invoice_type_display()
            uploaded_by = f"{invoice.submitted_by.get_full_name()} ({invoice.submitted_by.email})" if invoice.submitted_by else "Unknown User"
            status_display = invoice.get_status_display()
            
            # Simple HTML table format - minimal styling for deliverability
            html_content = f"""
<p><strong>INVOICE APPROVAL REQUIRED - {level_text}</strong></p>

<p>Invoice {invoice.invoice_number} requires your approval.</p>

<p><strong>Invoice Details:</strong></p>

<table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse;">
  <tr>
    <td><strong>Invoice Number</strong></td>
    <td>{invoice.invoice_number}</td>
  </tr>
  <tr>
    <td><strong>Vendor</strong></td>
    <td>{vendor_display}</td>
  </tr>
  <tr>
    <td><strong>Amount</strong></td>
    <td>{currency_display} {amount_display}</td>
  </tr>
  <tr>
    <td><strong>Type</strong></td>
    <td>{type_display}</td>
  </tr>
  <tr>
    <td><strong>Uploaded By</strong></td>
    <td>{uploaded_by}</td>
  </tr>
  <tr>
    <td><strong>Status</strong></td>
    <td>{status_display}</td>
  </tr>
</table>

<p><strong>Action Required:</strong></p>
<p>Please review the attached PDF invoice and use the link below:</p>
<p><a href="{approval_page_url}">{approval_page_url}</a></p>

<p>---<br>This is an automated notification from RAD AI Finance System.</p>
"""
            
            # Plain text fallback
            plain_text_body = f"""INVOICE APPROVAL REQUIRED - {level_text}

Invoice {invoice.invoice_number} requires your approval.

Invoice Details:
- Invoice Number: {invoice.invoice_number}
- Vendor: {vendor_display}
- Amount: {currency_display} {amount_display}
- Type: {type_display}
- Uploaded By: {uploaded_by}
- Status: {status_display}

Action Required:
Please review the attached PDF and use this link: {approval_page_url}

---
This is an automated notification from RAD AI Finance System.
"""
            
            # Create email with HTML and plain text
            msg = EmailMultiAlternatives(
                subject,
                plain_text_body,
                self.from_email,
                [approval.approver_email],
                cc=cc_emails if cc_emails else None
            )
            msg.attach_alternative(html_content, "text/html")
            
            # Attach PDF if file exists
            if invoice.file_path and os.path.exists(invoice.file_path):
                with open(invoice.file_path, 'rb') as pdf_file:
                    msg.attach(
                        invoice.original_filename,
                        pdf_file.read(),
                        'application/pdf'
                    )
            
            msg.send()
            
            logger.info(f"Approval email sent to {approval.approver_email} for invoice {invoice.invoice_number}"
                       + (f" with CC: {', '.join(cc_emails)}" if cc_emails else ""))
            return True
            
        except Exception as e:
            logger.error(f"Failed to send approval email: {e}")
            return False
    
    def send_approval_confirmation(self, invoice, approver_email, approver_name, next_level_info=None):
        """Send confirmation email to approver after they approve"""
        try:
            subject = f"✅ Confirmation: You Approved Invoice {invoice.invoice_number}"
            
            # Safe display values
            try:
                amount_display = f"{invoice.total_amount:,.2f}" if invoice.total_amount else "0.00"
            except:
                amount_display = str(invoice.total_amount or "0.00")
            
            vendor_display = invoice.vendor_name or "N/A"
            currency_display = invoice.currency or "AED"
            
            # Next step message
            if next_level_info:
                next_step = f"<p style='background: #d4edda; padding: 15px; border-left: 4px solid #28a745; margin: 20px 0;'><strong>✉️ Next Step:</strong> Approval email has been sent to <strong>{next_level_info}</strong> with PDF attachment and approval buttons.</p>"
            else:
                next_step = f"<p style='background: #d4edda; padding: 15px; border-left: 4px solid #28a745; margin: 20px 0;'><strong>✅ All Done:</strong> This was the final approval. Invoice is now fully approved!</p>"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5;">
                <div style="max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <div style="text-align: center; margin-bottom: 30px;">
                        <div style="font-size: 64px; margin-bottom: 20px;">✅</div>
                        <h2 style="color: #28a745; margin: 0;">Approval Confirmed</h2>
                    </div>
                    
                    <p style="font-size: 16px; color: #333;">Dear <strong>{approver_name}</strong>,</p>
                    
                    <p style="font-size: 16px; color: #333;">Thank you for approving the following invoice:</p>
                    
                    <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr style="background-color: #f8f9fa;">
                                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Invoice Number</td>
                                <td style="padding: 10px; border: 1px solid #dee2e6;">{invoice.invoice_number}</td>
                            </tr>
                            <tr>
                                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Vendor</td>
                                <td style="padding: 10px; border: 1px solid #dee2e6;">{vendor_display}</td>
                            </tr>
                            <tr style="background-color: #f8f9fa;">
                                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Amount</td>
                                <td style="padding: 10px; border: 1px solid #dee2e6;"><strong>{currency_display} {amount_display}</strong></td>
                            </tr>
                            <tr>
                                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Your Decision</td>
                                <td style="padding: 10px; border: 1px solid #dee2e6;"><span style="color: #28a745; font-weight: bold;">APPROVED ✓</span></td>
                            </tr>
                        </table>
                    </div>
                    
                    {next_step}
                    
                    <p style="color: #6c757d; font-size: 14px; margin-top: 30px; text-align: center;">
                        This is an automated confirmation from RAD AI Finance System.<br>
                        No action required from you.
                    </p>
                </div>
            </body>
            </html>
            """
            
            msg = EmailMultiAlternatives(
                subject,
                f"You have approved invoice {invoice.invoice_number}. Amount: {currency_display} {amount_display}",
                self.from_email,
                [approver_email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            
            logger.info(f"Confirmation email sent to {approver_email} for approving invoice {invoice.invoice_number}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send approval confirmation email: {e}")
            return False
    
    def send_approval_notification(self, invoice, approver_name, decision):
        """Notify stakeholders of approval decision"""
        try:
            subject = f"Invoice {decision.upper()}: {invoice.invoice_number}"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: {'#28a745' if decision == 'approved' else '#dc3545'};">
                    Invoice {decision.upper()}
                </h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                    <p><strong>Invoice Number:</strong> {invoice.invoice_number}</p>
                    <p><strong>Vendor:</strong> {invoice.vendor_name or 'N/A'}</p>
                    <p><strong>Amount:</strong> {invoice.currency} {invoice.total_amount or 0}</p>
                    <p><strong>Approver:</strong> {approver_name}</p>
                    <p><strong>Decision:</strong> {decision.upper()}</p>
                </div>
            </body>
            </html>
            """
            
            # Send to finance team
            finance_email = getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')
            
            msg = EmailMultiAlternatives(
                subject,
                f"Invoice {invoice.invoice_number} has been {decision}.",
                self.from_email,
                [finance_email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send notification email: {e}")
            return False
    
    def send_invoice_upload_notification(self, invoice, uploaded_by, first_approval=None):
        """
        Send notification when invoice is uploaded via website
        Notifies procurement (Richa) and finance team
        Now includes approval button if recipient is the first approver
        """
        try:
            # Get Richa's email from settings
            richa_email = getattr(settings, 'FINANCE_RICHA_EMAIL', None)
            finance_email = getattr(settings, 'FINANCE_EMAIL', None)
            
            logger.info(f"📧 EMAIL NOTIFICATION START")
            logger.info(f"   Richa Email: {richa_email}")
            logger.info(f"   Finance Email: {finance_email}")
            logger.info(f"   From Email: {self.from_email}")
            
            # Collect recipients
            recipients = []
            if richa_email and richa_email.strip():
                recipients.append(richa_email)
            if finance_email and finance_email.strip() and finance_email != richa_email:
                recipients.append(finance_email)
            
            logger.info(f"   Recipients: {recipients}")
            
            # If no recipients configured, skip
            if not recipients:
                logger.warning("❌ No notification recipients configured for invoice uploads")
                return False
            
            # Build invoice review URL
            base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            invoice_url = f"{base_url}/finance/invoices/{invoice.id}"
            
            # Build approval button HTML for each recipient
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            
            # Safe formatting for amount (handle None, Decimal, string)
            try:
                amount_display = f"{float(invoice.total_amount):,.2f}" if invoice.total_amount else "0.00"
            except (ValueError, TypeError, AttributeError):
                amount_display = "0.00"
            
            # Safe formatting for vendor name
            vendor_display = invoice.vendor_name if invoice.vendor_name else 'N/A'
            
            # Safe formatting for invoice type
            try:
                type_display = invoice.get_invoice_type_display()
            except AttributeError:
                type_display = str(invoice.invoice_type) if hasattr(invoice, 'invoice_type') else 'N/A'
            
            # Safe formatting for status
            try:
                status_display = invoice.get_status_display()
            except AttributeError:
                status_display = str(invoice.status) if hasattr(invoice, 'status') else 'N/A'
            
            # Safe formatting for currency
            currency_display = invoice.currency if hasattr(invoice, 'currency') and invoice.currency else 'AED'
            
            subject = f"New Invoice Uploaded - {invoice.invoice_number}"
            
            # Send individual emails to each recipient with personalized approval button
            for recipient in recipients:
                approval_section_html = ""
                approval_section_text = ""
                
                # Check if this recipient has a pending Level 1 approval
                if first_approval and first_approval.approver_email == recipient:
                    approval_page_url = f"{frontend_url}/finance/approve/{first_approval.approval_token}"
                    approval_section_html = f"""
<p><strong>ACTION REQUIRED - Level {first_approval.approval_level} Approval</strong></p>
<p>This invoice requires your approval to proceed in the workflow.</p>
<p><a href="{approval_page_url}">{approval_page_url}</a></p>
<p>Please review the attached PDF and submit your approval decision.</p>
"""
                    approval_section_text = f"""
ACTION REQUIRED - Level {first_approval.approval_level} Approval

This invoice requires your approval to proceed in the workflow.
Approval Link: {approval_page_url}

Please review the attached PDF and submit your approval decision.
"""
            
                # Simple HTML table format
                html_content = f"""
<p><strong>NEW INVOICE UPLOADED</strong></p>

<p>A new invoice has been uploaded to the system and is ready for processing.</p>

<p><strong>Invoice Details:</strong></p>

<table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse;">
  <tr>
    <td><strong>Invoice Number</strong></td>
    <td>{invoice.invoice_number}</td>
  </tr>
  <tr>
    <td><strong>Vendor</strong></td>
    <td>{vendor_display}</td>
  </tr>
  <tr>
    <td><strong>Amount</strong></td>
    <td>{currency_display} {amount_display}</td>
  </tr>
  <tr>
    <td><strong>Type</strong></td>
    <td>{type_display}</td>
  </tr>
  <tr>
    <td><strong>Uploaded By</strong></td>
    <td>{uploaded_by}</td>
  </tr>
  <tr>
    <td><strong>Status</strong></td>
    <td>{status_display}</td>
  </tr>
</table>

{approval_section_html}

<p><a href="{invoice_url}">View Invoice Details</a></p>

<p>---<br>The invoice is now in the approval workflow. You will receive approval requests as configured.<br>
This is an automated notification from RAD AI Finance System.</p>
"""
                
                # Plain text fallback
                plain_text_body = f"""NEW INVOICE UPLOADED

A new invoice has been uploaded to the system and is ready for processing.

Invoice Details:
- Invoice Number: {invoice.invoice_number}
- Vendor: {vendor_display}
- Amount: {currency_display} {amount_display}
- Type: {type_display}
- Uploaded By: {uploaded_by}
- Status: {status_display}

{approval_section_text}

View Invoice Details: {invoice_url}

---
The invoice is now in the approval workflow. You will receive approval requests as configured.
This is an automated notification from RAD AI Finance System.
"""
                
                # Send individual email with HTML and plain text fallback
                msg = EmailMultiAlternatives(
                    subject,
                    plain_text_body,
                    self.from_email,
                    [recipient]
                )
                msg.attach_alternative(html_content, "text/html")
                
                # Attach PDF if file exists
                if invoice.file_path and os.path.exists(invoice.file_path):
                    with open(invoice.file_path, 'rb') as pdf_file:
                        msg.attach(
                            invoice.original_filename,
                            pdf_file.read(),
                            'application/pdf'
                        )
                
                msg.send()
                logger.info(f"✅ Upload notification sent to {recipient} for invoice {invoice.invoice_number}" + 
                          (" (with approval button)" if approval_button_html else ""))
            
            logger.info(f"📧 EMAIL NOTIFICATION COMPLETE - Sent to {len(recipients)} recipients")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to send upload notification: {e}", exc_info=True)
            return False
    
    def send_error_notification(self, invoice_number, error_type, error_message):
        """Send error notification to finance team"""
        try:
            subject = f"Invoice Processing Error: {invoice_number}"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #dc3545;">Invoice Processing Error</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                    <p><strong>Invoice Number:</strong> {invoice_number}</p>
                    <p><strong>Error Type:</strong> {error_type}</p>
                    <p><strong>Error Message:</strong> {error_message}</p>
                </div>
                
                <p>Please review and process manually.</p>
            </body>
            </html>
            """
            
            finance_email = getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')
            
            msg = EmailMultiAlternatives(
                subject,
                f"Error processing invoice {invoice_number}: {error_message}",
                self.from_email,
                [finance_email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")
            return False
    
    def send_rejection_to_vendor(self, invoice, rejection_reason, rejected_by):
        """Send rejection notification to vendor/submitter"""
        try:
            # Determine recipient - use email_from if available, otherwise finance email
            recipient_email = invoice.email_from or getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')
            
            subject = f"Invoice Rejected - {invoice.invoice_number}"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
                <div style="background: #dc3545; color: white; padding: 20px; border-radius: 5px 5px 0 0;">
                    <h2 style="margin: 0;">❌ Invoice Rejected</h2>
                </div>
                
                <div style="border: 2px solid #dc3545; border-top: none; padding: 20px; border-radius: 0 0 5px 5px;">
                    <p>Dear Vendor,</p>
                    
                    <p>Your invoice has been <strong>rejected</strong> by our approval team.</p>
                    
                    <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                        <p><strong>Invoice Number:</strong> {invoice.invoice_number}</p>
                        <p><strong>Vendor Name:</strong> {invoice.vendor_name or 'N/A'}</p>
                        <p><strong>Total Amount:</strong> {invoice.currency} {invoice.total_amount or 'N/A'}</p>
                        <p><strong>Rejected By:</strong> {rejected_by}</p>
                        <p><strong>Rejection Reason:</strong></p>
                        <p style="background: white; padding: 10px; border-left: 3px solid #dc3545;">{rejection_reason or 'No reason provided'}</p>
                    </div>
                    
                    <p>Please review the rejection reason and resubmit if necessary.</p>
                    
                    <p>If you have any questions, please contact our finance team at {getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')}</p>
                    
                    <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                        This is an automated message from RAD AI Finance System.
                    </p>
                </div>
            </body>
            </html>
            """
            
            msg = EmailMultiAlternatives(
                subject,
                f"Invoice {invoice.invoice_number} has been rejected. Reason: {rejection_reason}",
                self.from_email,
                [recipient_email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            
            logger.info(f"Rejection email sent to {recipient_email} for invoice {invoice.invoice_number}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send rejection email to vendor: {e}", exc_info=True)
            return False
