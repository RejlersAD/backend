"""
Script to create Super Admin user for AIFlow
Email: tanzeem.agra@rejlers.ae
Password: Tanzeem@123
"""

import os
import sys
import django

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

User = get_user_model()

def create_super_admin():
    """Create or update super admin user"""
    
    email = "tanzeem.agra@rejlers.ae"
    password = "Tanzeem@123"
    
    print("=" * 60)
    print("AIFlow Super Admin Setup")
    print("=" * 60)
    
    try:
        # Check if user exists
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'first_name': 'Tanzeem',
                'last_name': 'Agra',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            }
        )
        
        if created:
            print(f"✅ Created new super admin user: {email}")
        else:
            print(f"📝 Updating existing user: {email}")
            user.first_name = 'Tanzeem'
            user.last_name = 'Agra'
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
        
        # Set password
        user.set_password(password)
        user.save()
        print(f"✅ Password set successfully")
        
        # Grant all permissions
        all_permissions = Permission.objects.all()
        user.user_permissions.set(all_permissions)
        print(f"✅ Granted {all_permissions.count()} permissions")
        
        # Create custom admin permissions if they don't exist
        content_type = ContentType.objects.get_for_model(User)
        
        admin_permissions = [
            ('view_admin_dashboard', 'Can view admin dashboard'),
            ('view_user_management', 'Can view user management'),
        ]
        
        for codename, name in admin_permissions:
            permission, created = Permission.objects.get_or_create(
                codename=codename,
                content_type=content_type,
                defaults={'name': name}
            )
            user.user_permissions.add(permission)
            if created:
                print(f"✅ Created permission: {name}")
        
        print("\n" + "=" * 60)
        print("Super Admin Account Details:")
        print("=" * 60)
        print(f"Email:        {email}")
        print(f"Password:     {password}")
        print(f"Name:         {user.first_name} {user.last_name}")
        print(f"Staff:        {user.is_staff}")
        print(f"Superuser:    {user.is_superuser}")
        print(f"Active:       {user.is_active}")
        print(f"Permissions:  {user.user_permissions.count()}")
        print("=" * 60)
        print("✅ Super Admin setup completed successfully!")
        print("=" * 60)
        
        return user
        
    except Exception as e:
        print(f"❌ Error creating super admin: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    create_super_admin()
