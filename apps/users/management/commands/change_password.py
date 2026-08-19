"""
Smart User Password Change Management Command
Django management command to change user passwords using soft coding principles
"""
import logging
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Django management command to change user password
    
    Usage:
        # Change password for a specific user
        python manage.py change_password --email mitul.patel@rejlers.ae --password "Mitul@123"
        
        # With additional options
        python manage.py change_password --email mitul.patel@rejlers.ae --password "Mitul@123" --must-reset
        
        # Interactive mode (prompts for password)
        python manage.py change_password --email mitul.patel@rejlers.ae --interactive
    """
    
    help = 'Change password for a user account (Smart & Secure)'
    
    def add_arguments(self, parser):
        """Define command arguments"""
        parser.add_argument(
            '--email',
            type=str,
            required=True,
            help='Email address of the user'
        )
        
        parser.add_argument(
            '--password',
            type=str,
            required=False,
            help='New password (use --interactive if you want to hide input)'
        )
        
        parser.add_argument(
            '--must-reset',
            action='store_true',
            help='User must reset password on next login'
        )
        
        parser.add_argument(
            '--temp-password',
            action='store_true',
            help='Mark this as a temporary password (sets is_first_login=True)'
        )
        
        parser.add_argument(
            '--interactive',
            action='store_true',
            help='Prompt for password interactively (hides input)'
        )
    
    def handle(self, *args, **options):
        """Execute the command"""
        email = options['email']
        password = options['password']
        must_reset = options['must_reset']
        is_temp = options['temp_password']
        interactive = options['interactive']
        
        # Header
        self.stdout.write('=' * 80)
        self.stdout.write(self.style.SUCCESS('🔐 SMART USER PASSWORD CHANGE UTILITY'))
        self.stdout.write('=' * 80)
        
        # Validate password input
        if not password and not interactive:
            raise CommandError(
                'Password is required. Use --password "YourPassword" or --interactive'
            )
        
        # Interactive password input
        if interactive:
            from getpass import getpass
            password = getpass('Enter new password: ')
            password_confirm = getpass('Confirm new password: ')
            
            if password != password_confirm:
                raise CommandError('Passwords do not match!')
        
        if not password:
            raise CommandError('Password cannot be empty!')
        
        # Configuration display
        self.stdout.write('\n📝 Configuration:')
        self.stdout.write(f'   Target User: {email}')
        self.stdout.write(f'   Password Length: {len(password)} characters')
        self.stdout.write(f'   Must Reset on Login: {must_reset}')
        self.stdout.write(f'   Temporary Password: {is_temp}')
        
        try:
            result = self._change_password(
                email=email,
                new_password=password,
                must_reset=must_reset,
                is_temp=is_temp
            )
            
            if result['success']:
                self.stdout.write('\n' + '=' * 80)
                self.stdout.write(self.style.SUCCESS('✅ PASSWORD CHANGE COMPLETED SUCCESSFULLY'))
                self.stdout.write('=' * 80)
                self.stdout.write('\n🎯 Summary:')
                self.stdout.write(f'   User: {email}')
                self.stdout.write(f'   Status: Changed')
                self.stdout.write(f'   Timestamp: {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}')
                
                self.stdout.write('\n💡 Next Steps:')
                self.stdout.write('   - User can now login with the new password')
                if must_reset:
                    self.stdout.write('   - User MUST reset password on next login')
                if is_temp:
                    self.stdout.write('   - Marked as temporary password')
                
                logger.info(f"Password changed for user: {email}")
            else:
                raise CommandError(result['message'])
                
        except Exception as e:
            logger.error(f"Password change failed for {email}: {str(e)}")
            raise CommandError(f'Failed to change password: {str(e)}')
    
    @transaction.atomic
    def _change_password(self, email, new_password, must_reset=False, is_temp=False):
        """
        Change password for a user with proper Django security
        
        Args:
            email (str): User's email address
            new_password (str): New password to set
            must_reset (bool): Whether user must reset password on next login
            is_temp (bool): Whether this is a temporary password
        
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
                result['message'] = f'User with email "{email}" not found!'
                return result
            
            self.stdout.write(f'\n{"=" * 80}')
            self.stdout.write(f'CHANGING PASSWORD FOR: {email}')
            self.stdout.write('=' * 80)
            
            self.stdout.write('\n📋 User Information:')
            self.stdout.write(f'   Email: {user.email}')
            self.stdout.write(f'   Username: {user.username}')
            self.stdout.write(f'   Name: {user.first_name} {user.last_name}')
            self.stdout.write(f'   Active: {user.is_active}')
            self.stdout.write(f'   Staff: {user.is_staff}')
            
            # Store old password tracking info
            old_last_change = user.last_password_change
            
            # Set the new password using Django's secure method
            user.set_password(new_password)
            
            # Update password tracking fields
            user.last_password_change = timezone.now()
            user.must_reset_password = must_reset
            
            # Update first login flag
            if is_temp:
                user.is_first_login = True
                user.temp_password_created_at = timezone.now()
            else:
                user.is_first_login = False
                user.temp_password_created_at = None
            
            # Save the user
            user.save()
            
            self.stdout.write(self.style.SUCCESS('\n✅ Password Changed Successfully!'))
            self.stdout.write('\n🔒 Security Settings:')
            self.stdout.write(f'   Must Reset Password: {user.must_reset_password}')
            self.stdout.write(f'   Is First Login: {user.is_first_login}')
            self.stdout.write(f'   Last Password Change: {user.last_password_change}')
            
            if old_last_change:
                self.stdout.write(f'   Previous Password Change: {old_last_change}')
            
            result['success'] = True
            result['message'] = f'Password changed successfully for {email}'
            result['user'] = user
            
        except Exception as e:
            result['message'] = f'Error: {str(e)}'
            self.stdout.write(self.style.ERROR(f'\n{result["message"]}'))
            import traceback
            traceback.print_exc()
        
        return result
