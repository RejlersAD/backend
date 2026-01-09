#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import ApprovalRoute

routes = ApprovalRoute.objects.filter(is_active=True)

print("\n=== CURRENT APPROVAL ROUTES ===\n")

for route in routes:
    print(f"{route.get_invoice_type_display().upper()} ({route.invoice_type}):")
    for level in route.approval_chain:
        print(f"  Level {level['level']}: {level['name']} - {level['email']}")
    print()
