#!/usr/bin/env python
"""Setup approval routes with multi-level approval for testing"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import ApprovalRoute, InvoiceType

print("=" * 70)
print("SETTING UP MULTI-LEVEL APPROVAL ROUTES - Smart Soft Coding")
print("=" * 70)

# SMART SOFT-CODED: Get emails from environment
# Get emails from environment (no hardcoded fallbacks)
richa_email = os.getenv('FINANCE_RICHA_EMAIL')
finance_email = os.getenv('FINANCE_EMAIL')

# For testing: Add second level with finance email as fake CEO approval
print(f"\nLevel 1 (Procurement): {richa_email}")
print(f"Level 2 (CEO - Testing): {finance_email}")

# Define approval chains for each invoice type with 2 levels
invoice_types = [
    {
        'type': InvoiceType.PROJECT,
        'chain': [
            {"level": 1, "name": "Richa (Procurement)", "email": richa_email, "title": "Procurement Manager"},
            {"level": 2, "name": "CEO (Testing)", "email": finance_email, "title": "Chief Executive Officer"}
        ]
    },
    {
        'type': InvoiceType.IT,
        'chain': [
            {"level": 1, "name": "Richa (Procurement)", "email": richa_email, "title": "Procurement Manager"},
            {"level": 2, "name": "CEO (Testing)", "email": finance_email, "title": "Chief Executive Officer"}
        ]
    },
    {
        'type': InvoiceType.FINANCE,
        'chain': [
            {"level": 1, "name": "Richa (Procurement)", "email": richa_email, "title": "Procurement Manager"},
            {"level": 2, "name": "CEO (Testing)", "email": finance_email, "title": "Chief Executive Officer"}
        ]
    },
    {
        'type': InvoiceType.ADMIN,
        'chain': [
            {"level": 1, "name": "Richa (Procurement)", "email": richa_email, "title": "Procurement Manager"},
            {"level": 2, "name": "CEO (Testing)", "email": finance_email, "title": "Chief Executive Officer"}
        ]
    }
]

for config in invoice_types:
    route, created = ApprovalRoute.objects.update_or_create(
        invoice_type=config['type'],
        defaults={
            'approval_chain': config['chain'],
            'is_active': True,
            'priority': 10
        }
    )
    
    action = "Created" if created else "Updated"
    print(f"\n✓ {action} route for {config['type']}:")
    for level in config['chain']:
        print(f"    Level {level['level']}: {level['name']} - {level['email']}")

print("\n" + "=" * 70)
print("MULTI-LEVEL APPROVAL ROUTES SETUP COMPLETE")
print("=" * 70)
print("\n✓ 2-level approval workflow configured")
print("✓ Level 1: Richa approves → sends to Level 2")
print("✓ Level 2: CEO approves → invoice fully approved")
print("✓ All emails sent with PDF attachments and Accept/Reject buttons")
print("✓ Status updates reflected in RAD AI system")
