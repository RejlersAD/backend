"""
Create users and assign modules for shareeq
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from apps.users.models import User
from apps.rbac.models import Module

# Create shareeq user
print("Creating shareeq user...")
user, created = User.objects.get_or_create(
    email='shareeq@rejlers.ae',
    defaults={
        'first_name': 'Shareeq',
        'last_name': 'Khan',
        'is_active': True,
        'is_staff': False,
        'is_superuser': False,
    }
)

if created:
    user.set_password('shareeq123')  # Set a default password
    user.save()
    print(f"✅ Created user: {user.email}")
else:
    print(f"ℹ️  User already exists: {user.email}")

# Get PFD and PID modules
pfd_module = Module.objects.filter(code='PFD').first()
pid_module = Module.objects.filter(code='PID').first()

if pfd_module:
    user.modules.add(pfd_module)
    print(f"✅ Added PFD module: {pfd_module.name}")
    
if pid_module:
    user.modules.add(pid_module)
    print(f"✅ Added PID module: {pid_module.name}")

# Show final modules
print(f"\n📦 Final modules for {user.email}:")
for module in user.modules.all():
    print(f"   ✓ {module.code}: {module.name}")

print("\n✅ Done!")
