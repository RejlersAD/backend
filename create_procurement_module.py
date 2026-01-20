#!/usr/bin/env python
"""
Script to create procurement RBAC module
Run this with: docker exec radai_backend_local python create_procurement_module.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module

# Create procurement module
module, created = Module.objects.get_or_create(
    code='procurement',
    defaults={
        'name': 'Procurement Management',
        'description': 'Vendor management, purchase requisitions, purchase orders, and goods receipt tracking',
        'is_active': True,
        'order': 13  # After designiq (12)
    }
)

if created:
    print(f"✅ Created Procurement module: {module.id}")
    print(f"   - Name: {module.name}")
    print(f"   - Code: {module.code}")
    print(f"   - Order: {module.order}")
else:
    print(f"ℹ️  Procurement module already exists: {module.id}")
    # Update fields if needed
    module.name = 'Procurement Management'
    module.description = 'Vendor management, purchase requisitions, purchase orders, and goods receipt tracking'
    module.is_active = True
    module.order = 13
    module.save()
    print("   - Updated module details")
