#!/usr/bin/env python
"""Smart cleanup - Remove all emails except approved ones"""
import os
import sys
import django
from pathlib import Path

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import Approval, ApprovalRoute
from django.conf import settings

# SMART SOFT-CODED: Get approved emails from .env file dynamically
approved_emails = set()

# Read .env file directly to get all FINANCE_*_EMAIL values
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # Check if this is a FINANCE email setting
                if 'FINANCE' in key and 'EMAIL' in key:
                    value = value.strip().strip('"').strip("'")
                    if value and '@' in value and not value.startswith('$'):
                        approved_emails.add(value.lower().strip())

# Also check EMAIL_HOST_USER and DEFAULT_FROM_EMAIL
for key in ['EMAIL_HOST_USER', 'DEFAULT_FROM_EMAIL', 'FINANCE_INBOX_EMAIL']:
    value = os.getenv(key) or getattr(settings, key, None)
    if value and '@' in str(value):
        approved_emails.add(str(value).lower().strip())

print("=" * 70)
print("SMART EMAIL CLEANUP - Using Soft Coding")
print("=" * 70)
print(f"\nApproved emails found in .env: {len(approved_emails)}")
for email in sorted(approved_emails):
    print(f"  ✓ {email}")

if not approved_emails:
    print("\n⚠ WARNING: No approved emails found! This would delete EVERYTHING.")
    print("⚠ Aborting to prevent data loss.")
    sys.exit(1)

# 1. Clean Approval table - DELETE any approval with non-approved email
print("\n" + "=" * 70)
print("1. Cleaning Approval Table")
print("=" * 70)

all_approvals = Approval.objects.all()
deleted_count = 0

for approval in all_approvals:
    email = approval.approver_email.lower().strip() if approval.approver_email else ""
    if email and email not in approved_emails:
        print(f"   ✗ Deleting: {approval.approver_email} (Level {approval.approval_level}, Invoice: {approval.invoice.invoice_number})")
        approval.delete()
        deleted_count += 1

if deleted_count == 0:
    print("   ✓ No unauthorized approvals found")
else:
    print(f"\n   Result: Deleted {deleted_count} approvals with unauthorized emails")

# 2. Clean ApprovalRoute approval_chain JSON - REMOVE levels with non-approved emails
print("\n" + "=" * 70)
print("2. Cleaning Approval Routes (approval_chain JSON)")
print("=" * 70)

routes = ApprovalRoute.objects.all()
cleaned_routes = 0
total_levels_removed = 0

for route in routes:
    if route.approval_chain:
        original_length = len(route.approval_chain)
        
        # Keep only levels with approved emails
        cleaned_chain = []
        for level in route.approval_chain:
            email = level.get('email', '').lower().strip()
            if email in approved_emails:
                cleaned_chain.append(level)
            else:
                print(f"   ✗ Removing from route {route.id} ({route.invoice_type}): {level.get('email')} - {level.get('name')}")
                total_levels_removed += 1
        
        if len(cleaned_chain) != original_length:
            route.approval_chain = cleaned_chain
            route.save()
            cleaned_routes += 1

if cleaned_routes == 0:
    print("   ✓ No unauthorized approval routes found")
else:
    print(f"\n   Result: Cleaned {cleaned_routes} routes, removed {total_levels_removed} unauthorized levels")

print("\n" + "=" * 70)
print("CLEANUP COMPLETE - Using Smart Soft Coding")
print("=" * 70)
print(f"\n✓ Kept {len(approved_emails)} approved emails, removed all others")
print("✓ No hardcoded email lists - fully dynamic from .env file")
print("✓ System is clean and ready for production")
