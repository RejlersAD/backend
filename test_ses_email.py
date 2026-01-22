#!/usr/bin/env python
"""
AWS SES Email Service Test
Tests AWS Simple Email Service connectivity and configuration
"""
import os
import sys
import django
from django.conf import settings

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.mail import send_mail, EmailMessage
from django.core import mail
import smtplib
from email.mime.text import MIMEText

print('=' * 70)
print('AWS SES EMAIL SERVICE TEST')
print('=' * 70)

# Display current configuration
print('\n--- Current Email Configuration ---')
print(f'Backend: {settings.EMAIL_BACKEND}')
print(f'Host: {settings.EMAIL_HOST}')
print(f'Port: {settings.EMAIL_PORT}')
print(f'Use TLS: {settings.EMAIL_USE_TLS}')
print(f'Use SSL: {settings.EMAIL_USE_SSL}')
print(f'Host User: {settings.EMAIL_HOST_USER if settings.EMAIL_HOST_USER else "Not Set"}')
print(f'Host Password: {"*" * 8 if settings.EMAIL_HOST_PASSWORD else "Not Set"}')
print(f'Default From: {settings.DEFAULT_FROM_EMAIL}')
print(f'Timeout: {settings.EMAIL_TIMEOUT}s')

# Check if using console backend
if settings.EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend':
    print('\n⚠️  WARNING: Using console backend (emails will print to console)')
    print('   To test AWS SES, set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend')
    print('   and provide EMAIL_HOST_USER and EMAIL_HOST_PASSWORD')

# Check if credentials are set
credentials_ok = bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)

print('\n--- Credential Check ---')
if credentials_ok:
    print(f'✓ Email credentials configured')
    print(f'  User: {settings.EMAIL_HOST_USER}')
    print(f'  Password: {"*" * len(settings.EMAIL_HOST_PASSWORD)} ({len(settings.EMAIL_HOST_PASSWORD)} chars)')
else:
    print('✗ Email credentials NOT configured')
    print('  EMAIL_HOST_USER and EMAIL_HOST_PASSWORD must be set')

# Test SMTP connection
if credentials_ok and settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
    print('\n--- Testing SMTP Connection ---')
    try:
        # Test direct SMTP connection
        print(f'Connecting to {settings.EMAIL_HOST}:{settings.EMAIL_PORT}...')
        
        if settings.EMAIL_USE_TLS:
            smtp = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
            smtp.starttls()
        elif settings.EMAIL_USE_SSL:
            smtp = smtplib.SMTP_SSL(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
        else:
            smtp = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
        
        print('✓ Connected to SMTP server')
        
        # Try authentication
        print(f'Authenticating as {settings.EMAIL_HOST_USER}...')
        smtp.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
        print('✓ Authentication successful')
        
        smtp.quit()
        print('✓ SMTP connection test passed')
        
    except smtplib.SMTPAuthenticationError as e:
        print(f'✗ Authentication failed: {e}')
        print('  Check your EMAIL_HOST_USER and EMAIL_HOST_PASSWORD')
    except smtplib.SMTPException as e:
        print(f'✗ SMTP error: {e}')
    except Exception as e:
        print(f'✗ Connection error: {type(e).__name__}: {e}')

# Test sending email via Django
print('\n--- Testing Django Email Backend ---')
try:
    # Try to send a test email
    test_subject = '[TEST] AWS SES Connection Test'
    test_message = '''
    This is a test email from RADAI Application.
    
    If you receive this email, your AWS SES configuration is working correctly.
    
    Configuration Details:
    - SMTP Host: {host}
    - SMTP Port: {port}
    - TLS Enabled: {tls}
    - From: {from_email}
    
    Timestamp: {timestamp}
    '''.format(
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        tls=settings.EMAIL_USE_TLS,
        from_email=settings.DEFAULT_FROM_EMAIL,
        timestamp=__import__('datetime').datetime.now().isoformat()
    )
    
    from_email = settings.DEFAULT_FROM_EMAIL
    # Send to the from email (should be verified in SES)
    recipient_list = [settings.DEFAULT_FROM_EMAIL]
    
    print(f'Attempting to send test email...')
    print(f'  From: {from_email}')
    print(f'  To: {recipient_list}')
    print(f'  Subject: {test_subject}')
    
    # Send email
    result = send_mail(
        subject=test_subject,
        message=test_message,
        from_email=from_email,
        recipient_list=recipient_list,
        fail_silently=False,
    )
    
    if result == 1:
        print('✓ Test email sent successfully!')
        print(f'  Check inbox: {recipient_list[0]}')
    else:
        print('⚠️  Email send returned 0 (may not have been sent)')
        
except Exception as e:
    print(f'✗ Failed to send email: {type(e).__name__}: {e}')
    import traceback
    print('\nError details:')
    print(traceback.format_exc())

# Summary
print('\n' + '=' * 70)
print('TEST SUMMARY')
print('=' * 70)

if credentials_ok:
    print('✓ Credentials configured')
    if settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
        print('✓ Using SMTP backend (real emails)')
        print('\n📧 Check your email inbox for the test message')
    else:
        print('⚠️  Using console backend (no real emails sent)')
else:
    print('✗ Credentials not configured')
    print('\nTo enable AWS SES, add to .env.local:')
    print('  EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend')
    print('  EMAIL_HOST=email-smtp.me-central-1.amazonaws.com')
    print('  EMAIL_PORT=587')
    print('  EMAIL_USE_TLS=True')
    print('  EMAIL_HOST_USER=your_ses_smtp_username')
    print('  EMAIL_HOST_PASSWORD=your_ses_smtp_password')
    print('  DEFAULT_FROM_EMAIL=verified-sender@yourdomain.com')

print('\n' + '=' * 70)
