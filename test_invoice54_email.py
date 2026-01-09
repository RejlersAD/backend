#!/usr/bin/env python
"""Send Level 2 approval email for Invoice #54"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import Invoice, Approval
from apps.finance.services.email_service import EmailService

# Get Invoice #54
invoice = Invoice.objects.get(id=54)
print(f"Invoice: {invoice.invoice_number}")

# Get Level 2 approval for test.user6
level2_approval = invoice.approvals.filter(
    approval_level=2,
    approver_email='test.user6@rejlers.ae'
).first()

if not level2_approval:
    print("❌ No Level 2 approval found")
    exit(1)

print(f"Sending to: {level2_approval.approver_email}")

# Send email
email_service = EmailService()
result = email_service.send_approval_request(level2_approval, invoice)

if result:
    print(f"✅ Email sent successfully to {level2_approval.approver_email}")
else:
    print(f"❌ Failed to send email")
