"""
Django Management Command: Monitor Invoice Emails
Monitors finance email inbox for incoming invoices and processes them automatically
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.base import ContentFile
from apps.finance.models import Invoice, InvoiceStatus
from apps.finance.services.workflow_service import FinanceWorkflowService
from apps.finance.services.email_service import EmailService
import imaplib
import email
from email.header import decode_header
import os
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Monitor finance email inbox for incoming invoices and process them automatically'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=60,
            help='Check interval in seconds (default: 60)'
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run once and exit (default: continuous monitoring)'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        run_once = options['once']

        self.stdout.write(self.style.SUCCESS('Starting Invoice Email Monitor...'))
        self.stdout.write(f'Check interval: {interval} seconds')
        self.stdout.write(f'Mode: {"Single run" if run_once else "Continuous"}')

        while True:
            try:
                self.check_emails()
            except Exception as e:
                logger.error(f"Email monitoring error: {e}", exc_info=True)
                self.stdout.write(self.style.ERROR(f'Error: {e}'))

            if run_once:
                break

            time.sleep(interval)

    def check_emails(self):
        """Check finance inbox for new invoice emails"""
        # Get email configuration from settings
        imap_enabled = getattr(settings, 'FINANCE_INBOX_ENABLED', False)
        imap_host = getattr(settings, 'FINANCE_INBOX_HOST', 'imap.gmail.com')
        imap_port = getattr(settings, 'FINANCE_INBOX_PORT', 993)
        email_user = getattr(settings, 'FINANCE_INBOX_EMAIL', None)
        email_password = getattr(settings, 'FINANCE_INBOX_PASSWORD', None)
        inbox_folder = getattr(settings, 'FINANCE_INBOX_FOLDER', 'INBOX')
        finance_email = getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')

        if not imap_enabled:
            self.stdout.write(self.style.WARNING('Email monitoring is disabled. Set FINANCE_INBOX_ENABLED=True to enable.'))
            return

        if not email_user or not email_password:
            self.stdout.write(self.style.WARNING('Email credentials not configured'))
            return

        try:
            # Connect to IMAP server
            self.stdout.write(f'Connecting to {imap_host}:{imap_port}...')
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(email_user, email_password)
            mail.select(inbox_folder)

            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')

            if status != 'OK':
                self.stdout.write(self.style.WARNING('No new emails found'))
                mail.logout()
                return

            email_ids = messages[0].split()

            if not email_ids:
                self.stdout.write(self.style.SUCCESS(f'[{datetime.now()}] No new invoice emails'))
                mail.logout()
                return

            self.stdout.write(self.style.SUCCESS(f'Found {len(email_ids)} new emails'))

            for email_id in email_ids:
                try:
                    self.process_email(mail, email_id)
                except Exception as e:
                    logger.error(f"Failed to process email {email_id}: {e}", exc_info=True)
                    self.stdout.write(self.style.ERROR(f'Failed to process email: {e}'))

            mail.logout()

        except Exception as e:
            logger.error(f"Email connection error: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f'Email connection error: {e}'))

    def process_email(self, mail, email_id):
        """Process a single email for invoice"""
        # Fetch email
        status, msg_data = mail.fetch(email_id, '(RFC822)')

        if status != 'OK':
            return

        # Parse email
        email_body = msg_data[0][1]
        email_message = email.message_from_bytes(email_body)

        # Decode subject
        subject = self.decode_subject(email_message['Subject'])
        from_email = email.utils.parseaddr(email_message['From'])[1]
        email_date = email.utils.parsedate_to_datetime(email_message['Date'])

        self.stdout.write(f'Processing email from {from_email}: {subject}')

        # Check for PDF attachments
        attachments = []
        for part in email_message.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            if part.get('Content-Disposition') is None:
                continue

            filename = part.get_filename()
            if filename and filename.lower().endswith('.pdf'):
                attachments.append({
                    'filename': filename,
                    'content': part.get_payload(decode=True)
                })

        # Handle missing attachment
        if not attachments:
            self.send_missing_attachment_email(from_email, subject)
            self.stdout.write(self.style.WARNING(f'No PDF attachment found in email from {from_email}'))
            # Mark as read
            mail.store(email_id, '+FLAGS', '\\Seen')
            return

        # Process each PDF attachment
        for attachment in attachments:
            try:
                self.process_invoice_attachment(
                    attachment['filename'],
                    attachment['content'],
                    from_email,
                    subject,
                    email_date
                )
            except Exception as e:
                logger.error(f"Failed to process attachment {attachment['filename']}: {e}", exc_info=True)

        # Mark email as read
        mail.store(email_id, '+FLAGS', '\\Seen')

    def decode_subject(self, subject):
        """Decode email subject"""
        if not subject:
            return "No Subject"

        decoded_parts = decode_header(subject)
        subject_text = ""

        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                subject_text += part.decode(encoding or 'utf-8', errors='ignore')
            else:
                subject_text += part

        return subject_text

    def process_invoice_attachment(self, filename, content, from_email, subject, email_date):
        """Process PDF attachment as invoice"""
        try:
            # Save PDF to media directory
            media_dir = os.path.join(settings.MEDIA_ROOT, 'invoices')
            os.makedirs(media_dir, exist_ok=True)

            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_filename = f"{timestamp}_{filename}"
            file_path = os.path.join(media_dir, safe_filename)

            # Save file
            with open(file_path, 'wb') as f:
                f.write(content)

            self.stdout.write(self.style.SUCCESS(f'Saved PDF: {safe_filename}'))

            # Create invoice record
            invoice = Invoice.objects.create(
                email_subject=subject[:500],
                email_from=from_email,
                email_date=email_date,
                invoice_number=f'TEMP-{timestamp}',  # Temporary, will be extracted
                original_filename=filename[:500],
                file_path=file_path,
                status=InvoiceStatus.UPLOADED
            )

            self.stdout.write(self.style.SUCCESS(f'Created invoice #{invoice.id}'))

            # Process invoice through workflow
            workflow_service = FinanceWorkflowService()
            success = workflow_service.process_invoice(invoice.id)

            if success:
                self.stdout.write(self.style.SUCCESS(f'✓ Invoice processed successfully: {invoice.invoice_number}'))
                # Forward to Richa (Procurement) - first approver
                self.forward_to_richa(invoice)
            else:
                self.stdout.write(self.style.ERROR(f'✗ Invoice processing failed: {invoice.invoice_number}'))

        except Exception as e:
            logger.error(f"Invoice processing error: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f'Invoice processing error: {e}'))

    def forward_to_richa(self, invoice):
        """Forward invoice to Richa (Procurement) with PDF attachment"""
        try:
            email_service = EmailService()
            richa_email = getattr(settings, 'RICHA_EMAIL', 'richa@company.com')
            finance_email = getattr(settings, 'FINANCE_EMAIL', 'finance@company.com')

            # Get first approval (should be Richa)
            first_approval = invoice.approvals.filter(approval_level=1).first()

            if first_approval:
                # Send approval request with PDF attachment
                email_service.send_approval_request(invoice, first_approval)
                self.stdout.write(self.style.SUCCESS(f'Forwarded to Richa: {richa_email}'))
            else:
                logger.warning(f'No first-level approval found for invoice {invoice.id}')

        except Exception as e:
            logger.error(f"Failed to forward to Richa: {e}", exc_info=True)

    def send_missing_attachment_email(self, to_email, original_subject):
        """Send email notifying sender that PDF attachment is missing"""
        try:
            from django.core.mail import send_mail

            subject = f"RE: {original_subject} - PDF Attachment Missing"
            message = f"""
Hello,

We received your email regarding an invoice, but no PDF attachment was found.

Original Subject: {original_subject}

Please resend your email with the invoice PDF attached.

If you have any questions, please contact our finance team.

Best regards,
RAD AI Finance Team
            """

            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [to_email],
                fail_silently=False,
            )

            self.stdout.write(self.style.SUCCESS(f'Sent missing attachment notification to {to_email}'))

        except Exception as e:
            logger.error(f"Failed to send missing attachment email: {e}", exc_info=True)
