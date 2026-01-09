#!/usr/bin/env python
"""Manual test to send Level 2 approval email to test.user6"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import Invoice, Approval
from apps.finance.services.email_service import EmailService

# Get Invoice #53
invoice = Invoice.objects.get(id=53)
print(f"Invoice: {invoice.invoice_number}")
print(f"Type: {invoice.invoice_type}")
print(f"Status: {invoice.status}")

# Get Level 2 approval for test.user6
level2_approval = invoice.approvals.filter(
    approval_level=2,
    approver_email='test.user6@rejlers.ae'
).first()

if not level2_approval:
    print("❌ No Level 2 approval found for test.user6@rejlers.ae")
    exit(1)

print(f"\nLevel 2 Approval:")
print(f"  Approver: {level2_approval.approver_name}")
print(f"  Email: {level2_approval.approver_email}")
print(f"  Status: {level2_approval.status}")
print(f"  Token: {level2_approval.approval_token}")

# Send email
print("\n📧 Sending approval request email...")
email_service = EmailService()
result = email_service.send_approval_request(level2_approval, invoice)

if result:
    print(f"✅ Email sent successfully to {level2_approval.approver_email}")
else:
    print(f"❌ Failed to send email to {level2_approval.approver_email}")
