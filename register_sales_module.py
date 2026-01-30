"""
Register Sales Module in Database
Run this script to add the sales module to the system
Usage: python manage.py shell < register_sales_module.py
"""

from apps.users.models import Module, ModuleCategory

# Create Sales Module if it doesn't exist
sales_module, created = Module.objects.get_or_create(
    code='sales',
    defaults={
        'name': 'Department of Sales',
        'description': 'Complete sales CRM with AI-powered pipeline management, client tracking, and intelligent forecasting',
        'category': ModuleCategory.FINANCE,
        'icon': '💼',
        'is_active': True,
        'order': 4
    }
)

if created:
    print("✅ Sales module created successfully!")
    print(f"   ID: {sales_module.id}")
    print(f"   Code: {sales_module.code}")
    print(f"   Name: {sales_module.name}")
    print(f"   Category: {sales_module.category}")
else:
    print("ℹ️  Sales module already exists")
    print(f"   ID: {sales_module.id}")
    print(f"   Code: {sales_module.code}")
    print(f"   Name: {sales_module.name}")
    
    # Update if needed
    updated = False
    if sales_module.name != 'Department of Sales':
        sales_module.name = 'Department of Sales'
        updated = True
    if sales_module.description != 'Complete sales CRM with AI-powered pipeline management, client tracking, and intelligent forecasting':
        sales_module.description = 'Complete sales CRM with AI-powered pipeline management, client tracking, and intelligent forecasting'
        updated = True
    if sales_module.icon != '💼':
        sales_module.icon = '💼'
        updated = True
    if sales_module.order != 4:
        sales_module.order = 4
        updated = True
    
    if updated:
        sales_module.save()
        print("   ✅ Module updated with latest configuration")

# Display all modules for verification
print("\n📋 Current Modules in System:")
for module in Module.objects.all().order_by('order'):
    print(f"   {module.order}. {module.name} ({module.code}) - {module.category}")
