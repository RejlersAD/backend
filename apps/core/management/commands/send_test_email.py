"""
Django management command to test email sending via AWS SES
"""
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
import smtplib


class Command(BaseCommand):
    help = 'Test AWS SES email configuration and send a test email'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to',
            type=str,
            help='Email address to send test email to (defaults to DEFAULT_FROM_EMAIL)',
        )
        parser.add_argument(
            '--check-only',
            action='store_true',
            help='Only check configuration without sending email',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('AWS SES EMAIL TEST'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))

        # Display configuration
        self.stdout.write('Configuration:')
        self.stdout.write(f'  Backend: {settings.EMAIL_BACKEND}')
        self.stdout.write(f'  Host: {settings.EMAIL_HOST}')
        self.stdout.write(f'  Port: {settings.EMAIL_PORT}')
        self.stdout.write(f'  TLS: {settings.EMAIL_USE_TLS}')
        self.stdout.write(f'  From: {settings.DEFAULT_FROM_EMAIL}')
        
        if settings.EMAIL_HOST_USER:
            self.stdout.write(f'  User: {settings.EMAIL_HOST_USER}')
        else:
            self.stdout.write(self.style.ERROR('  User: NOT CONFIGURED'))
            return

        if options['check_only']:
            self.stdout.write(self.style.SUCCESS('\n✓ Configuration check complete'))
            return

        # Send test email
        recipient = options.get('to') or settings.DEFAULT_FROM_EMAIL
        
        self.stdout.write(f'\nSending test email to: {recipient}')
        
        try:
            result = send_mail(
                subject='[TEST] AWS SES Email Service Test',
                message=f'''
This is a test email from RADAI Application.

Your AWS SES email service is working correctly!

Configuration:
- SMTP Host: {settings.EMAIL_HOST}
- SMTP Port: {settings.EMAIL_PORT}
- From Email: {settings.DEFAULT_FROM_EMAIL}

Timestamp: {__import__('datetime').datetime.now().isoformat()}
                ''',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
            
            if result == 1:
                self.stdout.write(self.style.SUCCESS('\n✓ Test email sent successfully!'))
                self.stdout.write(f'  Check inbox: {recipient}\n')
            else:
                self.stdout.write(self.style.WARNING('\n⚠ Email send returned 0'))
                
        except smtplib.SMTPAuthenticationError as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Authentication failed: {e}'))
            self.stdout.write('  Check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Failed to send email: {e}'))
