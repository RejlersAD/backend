#!/usr/bin/env python
"""
Test email notification for Test.User1@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import Invoice
from apps.finance.services.email_service import EmailService
from django.conf import settings

print("=" * 60)
print("Testing Finance Email Notification")
print("=" * 60)

# Check settings
print(f"\n✅ RICHA_EMAIL: {getattr(settings, 'FINANCE_RICHA_EMAIL', 'NOT SET')}")
print(f"✅ FINANCE_EMAIL: {getattr(settings, 'FINANCE_EMAIL', 'NOT SET')}")
print(f"✅ FRONTEND_URL: {getattr(settings, 'FRONTEND_URL', 'NOT SET')}")
print(f"✅ EMAIL_HOST_USER: {settings.EMAIL_HOST_USER}")
print(f"✅ EMAIL_HOST: {settings.EMAIL_HOST}")

# Get latest invoice
invoice = Invoice.objects.order_by('-id').first()
if not invoice:
    print("\n❌ No invoices found in database")
    exit(1)

print(f"\n📄 Latest Invoice: {invoice.invoice_number}")
print(f"   Vendor: {invoice.vendor_name}")
print(f"   Amount: {invoice.total_amount}")
print(f"   Status: {invoice.status}")

# Test email service
print("\n📧 Testing email notification...")
email_service = EmailService()

try:
    uploaded_by = f"{invoice.submitted_by.get_full_name()} ({invoice.submitted_by.email})" if invoice.submitted_by else "Test User"
    result = email_service.send_invoice_upload_notification(invoice, uploaded_by)
    
    if result:
        print("✅ Email notification sent successfully!")
    else:
        print("⚠️ Email notification returned False")
except Exception as e:
    print(f"❌ Error sending notification: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
