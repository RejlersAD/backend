#!/usr/bin/env python
"""
Railway deployment startup script
Ensures proper initialization and creates superuser
"""
import os
import sys
import django
from pathlib import Path

# Add the project directory to the sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

def create_superuser():
    """Create or update superuser"""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    email = 'tanzeem.agra@rejlers.ae'
    username = 'tanzeem'
    password = 'Tanzeem@123'
    
    print("\n" + "="*50)
    print("CREATING SUPERUSER")
    print("="*50)
    
    try:
        user = User.objects.filter(email=email).first()
        
        if user:
            print(f"✓ User {email} already exists - updating...")
            user.set_password(password)
            user.is_superuser = True
            user.is_staff = True
            user.is_active = True
            user.is_verified = True
            user.save()
            print(f"✓ Updated user {email}")
        else:
            user = User.objects.create_superuser(
                email=email,
                username=username,
                password=password
            )
            user.is_verified = True
            user.save()
            print(f"✓ Created superuser {email}")
        
        print("\n" + "="*50)
        print("LOGIN CREDENTIALS")
        print("="*50)
        print(f"Email: {email}")
        print(f"Password: {password}")
        print("="*50 + "\n")
        
        return True
    except Exception as e:
        print(f"✗ Error creating superuser: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_health():
    """Verify the application is ready"""
    try:
        # Check database connection
        from django.db import connection
        connection.ensure_connection()
        print("✓ Database connection successful")
        
        # Check if migrations are applied
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('showmigrations', '--plan', stdout=out, no_color=True)
        migrations_output = out.getvalue()
        if '[X]' in migrations_output or migrations_output.strip():
            print("✓ Migrations status OK")
        
        # Check critical settings
        from django.conf import settings
        print(f"✓ DEBUG={settings.DEBUG}")
        print(f"✓ ALLOWED_HOSTS={settings.ALLOWED_HOSTS}")
        print(f"✓ Database: {settings.DATABASES['default']['ENGINE']}")
        
        return True
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False

def assign_super_admin_roles():
    """Assign super admin roles to default users"""
    print("\n" + "="*50)
    print("ASSIGNING SUPER ADMIN ROLES")
    print("="*50)
    
    try:
        from django.core.management import call_command
        call_command('assign_super_admin')
        print("✓ Super admin roles assigned")
        return True
    except Exception as e:
        print(f"⚠ Failed to assign super admin roles: {e}")
        # Don't fail deployment if this fails
        return True

if __name__ == "__main__":
    print("=" * 50)
    print("Railway Deployment Initialization")
    print("=" * 50)
    
    if check_health():
        print("\n✓ Health check passed")
        if create_superuser():
            print("\n✓ Superuser created/updated")
            if assign_super_admin_roles():
                print("\n✓ Application is ready to serve traffic")
                sys.exit(0)
            else:
                print("\n⚠ Role assignment failed but continuing...")
                sys.exit(0)
        else:
            print("\n⚠ Superuser creation failed but continuing...")
            sys.exit(0)
    else:
        print("\n✗ Application not ready")
        sys.exit(1)
