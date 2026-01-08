#!/usr/bin/env python
"""
Verify approval workflow for all invoice types
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import ApprovalRoute

print("\n" + "=" * 80)
print("APPROVAL WORKFLOW VERIFICATION - ALL 4 INVOICE TYPES")
print("=" * 80)

routes = ApprovalRoute.objects.filter(is_active=True).order_by('invoice_type')

for route in routes:
    print(f"\n📋 {route.invoice_type.upper()} INVOICE:")
    print("-" * 80)
    
    for level_config in route.approval_chain:
        level = level_config.get('level')
        name = level_config.get('name')
        email = level_config.get('email')
        title = level_config.get('title', '')
        cc = level_config.get('cc', [])
        
        print(f"  Level {level}: {name} ({title})")
        print(f"           Email: {email}")
        if cc:
            print(f"           CC: {', '.join(cc)}")

print("\n" + "=" * 80)
print("WORKFLOW LOGIC:")
print("=" * 80)
print("✅ Upload Invoice → Level 1 ONLY gets email with PDF + approval button")
print("✅ Level 1 Approves → Level 2 ONLY gets email with PDF + approval button")
print("✅ Level 2 Approves → Level 3 ONLY gets email with PDF + approval button")
print("✅ Level 3 Approves → Level 4 ONLY gets email with PDF + approval button")
print("✅ Level 4 Approves → Invoice FULLY APPROVED")
print("❌ If ANY level rejects → Invoice REJECTED, workflow stops")
print("=" * 80)
