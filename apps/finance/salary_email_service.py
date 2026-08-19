"""
Salary Slip Email Service
Send salary slips to employees via email
SOFT-CODED for easy template customization
"""
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
import os
import logging

from .salary_models import SalarySlipEmail, EmailStatus, SalarySlipAuditLog

logger = logging.getLogger(__name__)


class SalarySlipEmailService:
    """
    Service class for sending salary slip emails
    SOFT-CODED: Email templates and configuration can be easily customized
    """
    
    # SOFT-CODED: Email Configuration
    EMAIL_CONFIG = {
        'from_email': settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else 'hr@radai.com',
        'from_name': 'RADAI HR Department',
        'subject_template': 'Salary Slip for {month} {year}',
        'reply_to': ['hr@radai.com'],
        'max_retries': 3,
    }
    
    def __init__(self):
        self.logger = logger
    
    def send_salary_slip_email(self, salary_slip, sent_by, custom_message=''):
        """
        Send salary slip to employee via email
        
        Args:
            salary_slip: SalarySlip instance
            sent_by: User instance who initiated the send
            custom_message: Optional custom message to include
        
        Returns:
            SalarySlipEmail instance
        """
        try:
            # Get employee details
            employee = salary_slip.employee_salary_info
            recipient_email = employee.user.email
            employee_name = employee.user.get_full_name() or employee.user.email
            
            # Generate month name
            month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December']
            month_name = month_names[salary_slip.month - 1]
            
            # Prepare subject
            subject = self.EMAIL_CONFIG['subject_template'].format(
                month=month_name,
                year=salary_slip.year
            )
            
            # Prepare email body
            context = {
                'employee_name': employee_name,
                'month': month_name,
                'year': salary_slip.year,
                'slip_number': salary_slip.slip_number,
                'net_salary': salary_slip.net_salary,
                'currency': salary_slip.currency,
                'custom_message': custom_message,
            }
            
            message_body = self._generate_email_body(context)
            
            # Create email
            email = EmailMessage(
                subject=subject,
                body=message_body,
                from_email=f"{self.EMAIL_CONFIG['from_name']} <{self.EMAIL_CONFIG['from_email']}>",
                to=[recipient_email],
                reply_to=self.EMAIL_CONFIG['reply_to']
            )
            
            # Attach PDF if available
            if salary_slip.pdf_file_path:
                pdf_full_path = os.path.join(settings.MEDIA_ROOT, salary_slip.pdf_file_path)
                if os.path.exists(pdf_full_path):
                    with open(pdf_full_path, 'rb') as pdf_file:
                        email.attach(
                            f"{salary_slip.slip_number}.pdf",
                            pdf_file.read(),
                            'application/pdf'
                        )
            
            # Send email
            email.send(fail_silently=False)
            
            # Create email delivery record
            email_record = SalarySlipEmail.objects.create(
                salary_slip=salary_slip,
                recipient_email=recipient_email,
                subject=subject,
                sent_at=timezone.now(),
                status=EmailStatus.SENT
            )
            
            # Log action
            SalarySlipAuditLog.objects.create(
                salary_slip=salary_slip,
                action='sent',
                performed_by=sent_by,
                description=f'Salary slip emailed to {recipient_email}'
            )
            
            self.logger.info(f"Salary slip {salary_slip.slip_number} sent to {recipient_email}")
            
            return email_record
            
        except Exception as e:
            self.logger.error(f"Failed to send email for slip {salary_slip.slip_number}: {str(e)}")
            
            # Create failed email record
            email_record = SalarySlipEmail.objects.create(
                salary_slip=salary_slip,
                recipient_email=employee.user.email,
                subject=subject,
                status=EmailStatus.FAILED,
                last_error=str(e)
            )
            
            raise
    
    def _generate_email_body(self, context):
        """
        Generate email body from template
        SOFT-CODED: Can be replaced with HTML template
        
        Args:
            context: dict with template variables
        
        Returns:
            str: Email body
        """
        body = f"""
Dear {context['employee_name']},

Your salary slip for {context['month']} {context['year']} is ready.

Salary Details:
- Slip Number: {context['slip_number']}
- Net Salary: {context['currency']} {context['net_salary']:,.2f}

{context['custom_message']}

Please find your detailed salary slip attached as a PDF.

If you have any questions or concerns regarding your salary, please contact the HR department.

Best regards,
RADAI HR Department

---
This is an automated email. Please do not reply to this email.
        """
        
        return body.strip()
    
    def retry_failed_emails(self):
        """
        Retry sending failed emails
        Can be run as a scheduled task
        """
        failed_emails = SalarySlipEmail.objects.filter(
            status=EmailStatus.FAILED,
            retry_count__lt=self.EMAIL_CONFIG['max_retries']
        )
        
        success_count = 0
        still_failed = 0
        
        for email_record in failed_emails:
            try:
                # Attempt resend
                self.send_salary_slip_email(
                    email_record.salary_slip,
                    sent_by=None  # System retry
                )
                email_record.status = EmailStatus.SENT
                email_record.sent_at = timezone.now()
                email_record.save()
                success_count += 1
                
            except Exception as e:
                email_record.retry_count += 1
                email_record.last_error = str(e)
                email_record.save()
                still_failed += 1
        
        self.logger.info(f"Email retry complete: {success_count} sent, {still_failed} still failed")
        
        return {
            'success_count': success_count,
            'failed_count': still_failed
        }
    
    def send_bulk_emails(self, salary_slips, sent_by, custom_message=''):
        """
        Send salary slips to multiple employees
        
        Args:
            salary_slips: List of SalarySlip instances
            sent_by: User instance
            custom_message: Optional custom message
        
        Returns:
            dict: Summary of sent/failed emails
        """
        results = {
            'success': [],
            'failed': [],
        }
        
        for salary_slip in salary_slips:
            try:
                self.send_salary_slip_email(salary_slip, sent_by, custom_message)
                results['success'].append(salary_slip.slip_number)
            except Exception as e:
                results['failed'].append({
                    'slip_number': salary_slip.slip_number,
                    'error': str(e)
                })
        
        return results
