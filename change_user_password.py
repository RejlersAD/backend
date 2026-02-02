"""
Smart User Password Change Script
Changes user password using soft coding techniques and Django best practices
"""
import os
import sys
import django
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

# ============================================================================
# CONFIGURATION - Change these values as needed (Soft Coding)
# ============================================================================

PASSWORD_CHANGE_CONFIG = {
    'user_email': 'mitul.patel@rejlers.ae',
    'new_password': 'Mitul@123',
    'mark_as_must_reset': False,  # Set to True if user must change password on next login
    'reset_first_login_flag': True,  # Set to False if this is a temp password
}

# ============================================================================
# PASSWORD CHANGE FUNCTION
# ============================================================================

def change_user_password(email, new_password, must_reset=False, is_not_first_login=True):
    """
    Change password for a user with proper Django security
    
    Args:
        email (str): User's email address
        new_password (str): New password to set
        must_reset (bool): Whether user must reset password on next login
        is_not_first_login (bool): Set to False if this is a temporary password
    
    Returns:
        dict: Result with success status and message
    """
    result = {
        'success': False,
        'message': '',
        'user': None
    }
    
    try:
        # Find the user
        user = User.objects.filter(email=email).first()
        
        if not user:
            result['message'] = f'❌ User with email "{email}" not found!'
            return result
        
        print(f"\n{'=' * 80}")
        print(f"CHANGING PASSWORD FOR: {email}")
        print('=' * 80)
        
        print(f"\n📋 User Information:")
        print(f"   Email: {user.email}")
        print(f"   Username: {user.username}")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Active: {user.is_active}")
        print(f"   Staff: {user.is_staff}")
        
        # Store old password tracking info
        old_last_change = user.last_password_change
        
        # Set the new password using Django's secure method
        user.set_password(new_password)
        
        # Update password tracking fields
        user.last_password_change = timezone.now()
        user.must_reset_password = must_reset
        
        # Update first login flag
        if is_not_first_login:
            user.is_first_login = False
            user.temp_password_created_at = None
        else:
            user.temp_password_created_at = timezone.now()
        
        # Save the user
        user.save()
        
        print(f"\n✅ Password Changed Successfully!")
        print(f"\n🔒 Security Settings:")
        print(f"   Must Reset Password: {user.must_reset_password}")
        print(f"   Is First Login: {user.is_first_login}")
        print(f"   Last Password Change: {user.last_password_change}")
        
        if old_last_change:
            print(f"   Previous Password Change: {old_last_change}")
        
        result['success'] = True
        result['message'] = f'Password changed successfully for {email}'
        result['user'] = user
        
    except Exception as e:
        result['message'] = f'❌ Error: {str(e)}'
        print(f"\n{result['message']}")
        import traceback
        traceback.print_exc()
    
    return result

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function"""
    print("\n" + "=" * 80)
    print("🔐 SMART USER PASSWORD CHANGE UTILITY")
    print("=" * 80)
    
    # Extract configuration
    email = PASSWORD_CHANGE_CONFIG['user_email']
    password = PASSWORD_CHANGE_CONFIG['new_password']
    must_reset = PASSWORD_CHANGE_CONFIG['mark_as_must_reset']
    reset_first_login = PASSWORD_CHANGE_CONFIG['reset_first_login_flag']
    
    print(f"\n📝 Configuration:")
    print(f"   Target User: {email}")
    print(f"   Password Length: {len(password)} characters")
    print(f"   Must Reset on Login: {must_reset}")
    print(f"   Mark as Not First Login: {reset_first_login}")
    
    # Confirm before proceeding
    print(f"\n⚠️  WARNING: This will change the password for {email}")
    
    # For automated execution, skip confirmation
    # For manual execution, uncomment the lines below:
    # response = input("\nProceed? (yes/no): ")
    # if response.lower() != 'yes':
    #     print("❌ Operation cancelled.")
    #     sys.exit(0)
    
    # Change the password
    result = change_user_password(
        email=email,
        new_password=password,
        must_reset=must_reset,
        is_not_first_login=reset_first_login
    )
    
    # Print final result
    print("\n" + "=" * 80)
    if result['success']:
        print("✅ PASSWORD CHANGE COMPLETED SUCCESSFULLY")
        print("=" * 80)
        print(f"\n🎯 Summary:")
        print(f"   User: {email}")
        print(f"   Status: Changed")
        print(f"   Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"\n💡 Next Steps:")
        print(f"   - User can now login with the new password")
        if must_reset:
            print(f"   - User MUST reset password on next login")
        if reset_first_login:
            print(f"   - First login flag has been cleared")
    else:
        print("❌ PASSWORD CHANGE FAILED")
        print("=" * 80)
        print(f"\n❌ Error: {result['message']}")
    print()

if __name__ == '__main__':
    main()
