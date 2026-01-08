"""
Email Service for Finance Module
Sends notifications and approval emails
"""
from django.core.mail import send_mail, EmailMultiAlternatives
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
    
    def send_approval_request(self, invoice, approval):
        """Send approval request email with approve/reject buttons and PDF attachment"""
        try:
            # Parse approval metadata for CC and title
            metadata = approval.approval_metadata or {}
            cc_emails = metadata.get('cc', [])
            title = metadata.get('title', '')
            mandatory = metadata.get('mandatory', False)
            
            # Build approval URLs
            base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            approve_url = f"{base_url}/finance/approve/{approval.approval_token}?action=approve"
            reject_url = f"{base_url}/finance/approve/{approval.approval_token}?action=reject"
            
            # Build level text
            level_text = f"Level {approval.approval_level}"
            if title:
                level_text += f" - {title}"
            if mandatory or 'CEO' in title.upper():
                level_text += " (MANDATORY)"
            
            subject = f"Invoice Approval Required ({level_text}) - {invoice.invoice_number}"
            
            # CC section for email body
            cc_text = ""
            if cc_emails:
                cc_text = f"<p><strong>CC:</strong> {', '.join(cc_emails)}</p>"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #2c3e50;">Invoice Approval Request - {level_text}</h2>
                {cc_text}
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                    <h3>Invoice Details:</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Invoice Number</td>
                            <td style="padding: 10px; border: 1px solid #dee2e6;">{invoice.invoice_number}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Vendor</td>
                            <td style="padding: 10px; border: 1px solid #dee2e6;">{invoice.vendor_name or 'N/A'}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Amount</td>
                            <td style="padding: 10px; border: 1px solid #dee2e6;"><strong>{invoice.currency} {invoice.total_amount or 0:,.2f}</strong></td>
                        </tr>
                        <tr>
                            <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Type</td>
                            <td style="padding: 10px; border: 1px solid #dee2e6;">{invoice.get_invoice_type_display()}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Date</td>
                            <td style="padding: 10px; border: 1px solid #dee2e6;">{invoice.invoice_date or 'N/A'}</td>
                        </tr>
                    </table>
                </div>
                
                <p><strong>Approval Level:</strong> {level_text}</p>
                
                <div style="margin: 30px 0;">
                    <a href="{approve_url}" style="background: #28a745; color: white; padding: 15px 40px; text-decoration: none; border-radius: 5px; margin-right: 15px; display: inline-block; font-size: 16px; font-weight: bold;">
                        ✓ APPROVE
                    </a>
                    <a href="{reject_url}" style="background: #dc3545; color: white; padding: 15px 40px; text-decoration: none; border-radius: 5px; display: inline-block; font-size: 16px; font-weight: bold;">
                        ✗ REJECT
                    </a>
                </div>
                
                <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                    Invoice PDF is attached for your review.<br>
                    This is an automated message from RAD AI Finance System.
                </p>
            </body>
            </html>
            """
            
            # Create email with attachment
            msg = EmailMultiAlternatives(
                subject,
                f"Invoice {invoice.invoice_number} requires your approval. Amount: {invoice.currency} {invoice.total_amount}",
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
