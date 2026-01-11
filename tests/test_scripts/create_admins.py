"""
Flexible admin user creation script for Railway PostgreSQL
Reads credentials from environment variables (soft-coded approach)
Run this with: python create_admins.py
"""
import os
import django
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model, authenticate

def create_or_update_admin(username, email, password, first_name, last_name):
    """Create or update an admin user with given credentials"""
    User = get_user_model()
    
    print(f'\n{"="*60}')
    print(f'Processing admin: {email}')
    print(f'{"="*60}')
    
    # Check if user exists by email
    if User.objects.filter(email=email).exists():
        user = User.objects.get(email=email)
        user.username = username
        user.set_password(password)
        user.first_name = first_name
        user.last_name = last_name
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.is_verified = True
        user.save()
        print(f'✓ Updated existing user: {email}')
    else:
        # Create new superuser
        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
        )
        user.is_verified = True
        user.save()
        print(f'✓ Created new superuser: {email}')
    
    # Display user status
    print(f'  - Username: {user.username}')
    print(f'  - Email: {user.email}')
    print(f'  - Name: {user.first_name} {user.last_name}')
    print(f'  - Active: {user.is_active}')
    print(f'  - Staff: {user.is_staff}')
    print(f'  - Superuser: {user.is_superuser}')
    print(f'  - Verified: {user.is_verified}')
    
    # Verify the password works
    auth_user = authenticate(email=email, password=password)
    if auth_user:
        print(f'✓ Authentication test PASSED')
    else:
        print(f'✗ Authentication test FAILED')
    
    return user

def main():
    """Main function to create all admin users from environment variables"""
    print('\n' + '='*60)
    print('ADMIN USER CREATION/UPDATE SCRIPT')
    print('='*60)
    
    # Get database connection info
    db_host = os.getenv('DB_HOST', 'Not set')
    db_name = os.getenv('DB_NAME', 'Not set')
    print(f'\nConnecting to database:')
    print(f'  - Host: {db_host}')
    print(f'  - Database: {db_name}')
    
    admins_created = []
    
    # Create Primary Admin
    admin1_email = os.getenv('ADMIN_EMAIL')
    if admin1_email:
        try:
            user = create_or_update_admin(
                username=os.getenv('ADMIN_USERNAME', 'admin'),
                email=admin1_email,
                password=os.getenv('ADMIN_PASSWORD', 'changeme'),
                first_name=os.getenv('ADMIN_FIRST_NAME', 'Admin'),
                last_name=os.getenv('ADMIN_LAST_NAME', 'User')
            )
            admins_created.append(admin1_email)
        except Exception as e:
            print(f'✗ Error creating primary admin: {e}')
    else:
        print('\n⚠ Primary admin credentials not found in environment')
    
    # Create Secondary Admin (if configured)
    admin2_email = os.getenv('ADMIN2_EMAIL')
    if admin2_email:
        try:
            user = create_or_update_admin(
                username=os.getenv('ADMIN2_USERNAME', 'admin2'),
                email=admin2_email,
                password=os.getenv('ADMIN2_PASSWORD', 'changeme'),
                first_name=os.getenv('ADMIN2_FIRST_NAME', 'Admin'),
                last_name=os.getenv('ADMIN2_LAST_NAME', 'User')
            )
            admins_created.append(admin2_email)
        except Exception as e:
            print(f'✗ Error creating secondary admin: {e}')
    
    # Summary
    print('\n' + '='*60)
    print('SUMMARY')
    print('='*60)
    print(f'Total admins processed: {len(admins_created)}')
    for email in admins_created:
        print(f'  ✓ {email}')
    print('\n✓ All operations completed successfully!')
    print('='*60 + '\n')

if __name__ == '__main__':
    main()
