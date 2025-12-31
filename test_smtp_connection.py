"""
Test SMTP connection and email sending with production credentials
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.mail import send_mail
from django.conf import settings
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

print("=" * 70)
print("SMTP CONNECTION AND EMAIL TEST")
print("=" * 70)

print("\n1. Current Configuration:")
print(f"   Email Host: {settings.EMAIL_HOST}")
print(f"   Email Port: {settings.EMAIL_PORT}")
print(f"   Use TLS: {settings.EMAIL_USE_TLS}")
print(f"   SMTP User: {settings.EMAIL_HOST_USER}")
print(f"   From Email: {settings.DEFAULT_FROM_EMAIL}")

print("\n2. Testing SMTP Connection...")
try:
    import smtplib
    server = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=10)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
    print("   ✅ SMTP connection successful!")
    print("   ✅ Authentication successful!")
    server.quit()
except Exception as e:
    print(f"   ❌ SMTP connection failed: {e}")
    sys.exit(1)

print("\n3. Testing Email Send to xerxes.in@gmail.com...")
try:
    from django.core.mail import EmailMultiAlternatives
    
    subject = "Test Email from AIFlow - SMTP Configuration Test"
    text_body = """
    Hello,
    
    This is a test email to verify the SMTP configuration.
    
    If you receive this email, the AWS SES SMTP credentials are working correctly.
    
    Best regards,
    AIFlow Team
    """
    
    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #2563eb;">SMTP Configuration Test</h2>
        <p>Hello,</p>
        <p>This is a test email to verify the SMTP configuration.</p>
        <p>If you receive this email, the AWS SES SMTP credentials are working correctly with <strong>production access</strong>.</p>
        <hr style="margin: 20px 0; border: none; border-top: 1px solid #e5e7eb;">
        <p style="color: #6b7280; font-size: 14px;">Best regards,<br>AIFlow Team</p>
    </body>
    </html>
    """
    
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=['xerxez.in@gmail.com']
    )
    
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)
    
    print("   ✅ Test email sent successfully!")
    print(f"   📧 Email sent to: xerxez.in@gmail.com")
    print(f"   📤 From: {settings.DEFAULT_FROM_EMAIL}")
    print("\n" + "=" * 70)
    print("SUCCESS! Check the inbox for xerxez.in@gmail.com")
    print("=" * 70)
    
except Exception as e:
    print(f"   ❌ Failed to send email: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
