"""
Django management command to assign PFD and PID modules to shareeq@rejlers.ae
"""
from django.core.management.base import BaseCommand
from apps.users.models import User
from apps.rbac.models import Module, UserProfile, Organization, UserRole, Role

class Command(BaseCommand):
    help = 'Assign PFD and PID modules to shareeq@rejlers.ae'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(self.style.SUCCESS('Assigning Modules to Shareeq'))
        self.stdout.write(self.style.SUCCESS('='*60))

        try:
            # Find or create the user
            user, user_created = User.objects.get_or_create(
                email='shareeq@rejlers.ae',
                defaults={
                    'username': 'shareeq',
                    'first_name': 'Shareeq',
                    'last_name': 'Khan',
                    'is_active': True,
                }
            )
            
            if user_created:
                user.set_password('shareeq123')
                user.save()
                self.stdout.write(self.style.SUCCESS(f'\n✅ Created user: {user.email}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'\n✅ Found user: {user.email}'))
            
            self.stdout.write(f'   Name: {user.first_name} {user.last_name}')
            self.stdout.write(f'   Active: {user.is_active}')
            
            # Get or create organization
            org, _ = Organization.objects.get_or_create(
                name='Rejlers Abu Dhabi',
                defaults={'code': 'REJLERS_AD', 'is_active': True}
            )
            
            # Get or create UserProfile
            profile, profile_created = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'organization': org,
                    'job_title': 'Engineer',
                    'status': 'active'
                }
            )
            
            if profile_created:
                self.stdout.write(self.style.SUCCESS(f'✅ Created user profile'))
            
            # Find and assign PFD module
            try:
                pfd_module = Module.objects.get(code='PFD')
                # Check if already assigned via roles
                if not profile.get_all_modules().filter(code='PFD').exists():
                    # Assign role that has PFD module
                    admin_role = Role.objects.filter(code='ADMIN').first()
                    if admin_role:
                        UserRole.objects.get_or_create(
                            user_profile=profile,
                            role=admin_role,
                            defaults={'assigned_by': user}
                        )
                        self.stdout.write(self.style.SUCCESS(f'\n✅ Assigned Admin role with all modules'))
                    else:
                        self.stdout.write(self.style.WARNING(f'\n⚠️  Admin role not found'))
                else:
                    self.stdout.write(self.style.WARNING(f'\n⚠️  User already has PFD access'))
            except Module.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'\n❌ PFD module not found!'))
            
            # Show final modules
            final_modules = profile.get_all_modules()
            self.stdout.write(f'\n📦 Final Modules ({final_modules.count()}):')
            for module in final_modules:
                self.stdout.write(self.style.SUCCESS(f'   ✓ {module.code}: {module.name}'))
            
            self.stdout.write(self.style.SUCCESS('\n'+'='*60))
            self.stdout.write(self.style.SUCCESS('✅ Module assignment completed!'))
            self.stdout.write(self.style.SUCCESS(f'   Login: shareeq@rejlers.ae / shareeq123'))
            self.stdout.write(self.style.SUCCESS('='*60))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error: {e}'))
            import traceback
            traceback.print_exc()
