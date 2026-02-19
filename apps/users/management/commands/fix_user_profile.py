"""
Management command to check and fix user profile issues
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Check and fix user profile issues for login'

    def handle(self, *args, **options):
        self.stdout.write('\n' + '='*70)
        self.stdout.write('Checking User Profile for Login Issues')
        self.stdout.write('='*70 + '\n')
        
        try:
            user = User.objects.get(email='tanzeem.agra@rejlers.ae')
            self.stdout.write(self.style.SUCCESS(f'✅ User found: {user.email}'))
            self.stdout.write(f'   User ID: {user.id}')
            self.stdout.write(f'   Is active: {user.is_active}')
            self.stdout.write(f'   Has usable password: {user.has_usable_password()}\n')
            
            try:
                profile = UserProfile.objects.get(user=user)
                self.stdout.write(self.style.SUCCESS('✅ UserProfile exists'))
                self.stdout.write(f'   Profile ID: {profile.id}')
                self.stdout.write(f'   Status: {profile.status}')
                self.stdout.write(f'   Is deleted: {profile.is_deleted}')
                
                if profile.is_deleted:
                    profile.is_deleted = False
                    profile.save()
                    self.stdout.write(self.style.WARNING('⚠️  Profile was marked as deleted, fixed now'))
                    
            except UserProfile.DoesNotExist:
                self.stdout.write(self.style.ERROR('❌ UserProfile DOES NOT EXIST!'))
                self.stdout.write(self.style.WARNING('   This is causing the 500 error on login'))
                self.stdout.write('   Creating UserProfile now...\n')
                
                profile = UserProfile.objects.create(
                    user=user,
                    status='active',
                    is_deleted=False
                )
                self.stdout.write(self.style.SUCCESS(f'✅ UserProfile created successfully'))
                self.stdout.write(f'   Profile ID: {profile.id}')
                self.stdout.write(f'   Status: {profile.status}\n')
                
            self.stdout.write(self.style.SUCCESS('\n✅ User is now ready to login!'))
            self.stdout.write('   Please try logging in with the credentials\n')
            
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR('❌ User not found: tanzeem.agra@rejlers.ae'))
            self.stdout.write('   Please create the user first\n')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error: {str(e)}'))
            import traceback
            traceback.print_exc()
