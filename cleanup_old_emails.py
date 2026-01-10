#!/usr/bin/env python
"""
Clean up old hardcoded emails from database
Removes approval routes with old gmail addresses
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import ApprovalRoute, Approval
from django.conf import settings

print("=" * 70)
print("CLEANING OLD HARDCODED EMAILS FROM DATABASE")
print("=" * 70)

# List of old emails to remove
OLD_EMAILS = [
    'rejlersabudhabi1@gmail.com',
    'khanabdullahomar5@gmail.com',
    'khanabdullahomar886+richa@gmail.com',
    'khanabdullahomar886+jamal@gmail.com',
    'khanabdullahomar886+rafat@gmail.com',
    'khanabdullahomar886+moe@gmail.com',
    'khanabdullahomar886+jarmo@gmail.com',
]

# Get current configured emails from settings
CURRENT_EMAILS = {
    'RICHA': getattr(settings, 'FINANCE_RICHA_EMAIL', 'test.user1@rejlers.ae'),
    'JAMAL': getattr(settings, 'JAMAL_EMAIL', 'test.user2@rejlers.ae'),
    'RAFAT': getattr(settings, 'RAFAT_EMAIL', 'test.user3@rejlers.ae'),
    'MOE': getattr(settings, 'MOE_EMAIL', 'test.user4@rejlers.ae'),
    'JARMO': getattr(settings, 'JARMO_EMAIL', 'test.user5@rejlers.ae'),
}

print("\n📋 Current Configured Emails:")
for name, email in CURRENT_EMAILS.items():
    print(f"   {name}: {email}")

print("\n🔍 Searching for old hardcoded emails in database...")

# Check ApprovalRoutes (emails are in approval_chain JSON field)
all_routes = ApprovalRoute.objects.all()
routes_with_old_emails = []

for route in all_routes:
    chain = route.approval_chain or []
    has_old_email = False
    for level in chain:
        if level.get('email') in OLD_EMAILS:
            has_old_email = True
            break
    if has_old_email:
        routes_with_old_emails.append(route)

if routes_with_old_emails:
    print(f"\n❌ Found {len(routes_with_old_emails)} ApprovalRoutes with old emails:")
    for route in routes_with_old_emails:
        print(f"   - {route.invoice_type}: Priority {route.priority}")
        for level in route.approval_chain:
            if level.get('email') in OLD_EMAILS:
                print(f"     • Level {level.get('level')}: {level.get('email')}")
    
    response = input("\n⚠️  Delete these old routes? (yes/no): ")
    if response.lower() == 'yes':
        for route in routes_with_old_emails:
            route.delete()
        print(f"✅ Deleted {len(routes_with_old_emails)} old approval routes")
    else:
        print("⏭️  Skipped deletion")
else:
    print("✅ No old approval routes found")

# Check pending Approvals
old_approvals = Approval.objects.filter(
    approver_email__in=OLD_EMAILS,
    status='pending'
)
old_approval_count = old_approvals.count()

if old_approval_count > 0:
    print(f"\n❌ Found {old_approval_count} pending Approvals with old emails:")
    for approval in old_approvals[:5]:  # Show first 5
        print(f"   - Invoice {approval.invoice.invoice_number}: {approval.approver_email}")
    
    if old_approval_count > 5:
        print(f"   ... and {old_approval_count - 5} more")
    
    response = input("\n⚠️  Cancel these old pending approvals? (yes/no): ")
    if response.lower() == 'yes':
        old_approvals.update(status='cancelled', comments='Cancelled - old email address')
        print(f"✅ Cancelled {old_approval_count} old pending approvals")
    else:
        print("⏭️  Skipped cancellation")
else:
    print("✅ No pending approvals with old emails")

# Summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Total ApprovalRoutes: {ApprovalRoute.objects.count()}")
print(f"Total Pending Approvals: {Approval.objects.filter(status='pending').count()}")
print("\n✅ Cleanup complete! System now uses only configured emails from .env")
print("=" * 70)
