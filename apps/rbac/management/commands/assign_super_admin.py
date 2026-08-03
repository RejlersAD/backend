"""
Management command to assign super admin role to a user
Automatically creates organization and profile if needed
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, UserProfile, UserRole, Organization

User = get_user_model()


class Command(BaseCommand):
    help = 'Assign super admin role to specified user(s)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='User email to make super admin',
        )

    def handle(self, *args, **options):
        email = options.get('email')
        
        # Default super admins if no email specified
        default_super_admins = [
            'tanzeem.agra@rejlers.ae',
            'admin@rejlers.com',
        ]
        
        emails_to_process = [email] if email else default_super_admins
        
        self.stdout.write(self.style.WARNING('\n' + '='*60))
        self.stdout.write(self.style.WARNING('ASSIGNING SUPER ADMIN ROLES'))
        self.stdout.write(self.style.WARNING('='*60))
        
        try:
            # Get super admin role
            super_admin_role = Role.objects.get(code='super_admin')
            self.stdout.write(f'✓ Found Super Admin role: {super_admin_role.name}')
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('✗ Super Admin role not found! Run seed_rbac first.'))
            return
        
        # Get or create default organization
        default_org, org_created = Organization.objects.get_or_create(
            code='default',
            defaults={
                'name': 'Default Organization',
                'primary_contact_email': 'admin@rejlers.com',
                'is_active': True
            }
        )
        if org_created:
            self.stdout.write(f'✓ Created default organization: {default_org.name}')
        else:
            self.stdout.write(f'✓ Using existing organization: {default_org.name}')
        
        for user_email in emails_to_process:
            try:
                # Get user
                user = User.objects.get(email=user_email)
                self.stdout.write(f'\n📧 Processing user: {user_email}')
                
                # Get or create user profile
                profile, created = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'organization': default_org,
                        'status': 'active'
                    }
                )
                
                if created:
                    self.stdout.write(f'  ✓ Created profile for: {user_email}')
                else:
                    self.stdout.write(f'  ✓ Found existing profile')
                
                # Update organization if missing
                if not profile.organization:
                    profile.organization = default_org
                    profile.save()
                    self.stdout.write(f'  ✓ Assigned to organization: {default_org.name}')
                
                # Ensure profile is active
                if profile.status != 'active':
                    profile.status = 'active'
                    profile.save()
                    self.stdout.write(f'  ✓ Activated profile')
                
                # Assign super admin role
                user_role, role_created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=super_admin_role,
                    defaults={'is_primary': True}
                )
                
                if role_created:
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✅ Assigned Super Admin role to: {user_email}'
                    ))
                else:
                    self.stdout.write(f'  ✓ Already has Super Admin role')
                
            except User.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠ User not found: {user_email} (will be created on first login)'
                ))
                continue
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'  ✗ Error processing {user_email}: {str(e)}'
                ))
                import traceback
                traceback.print_exc()
                continue
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('✅ SUPER ADMIN ASSIGNMENT COMPLETED'))
        self.stdout.write(self.style.SUCCESS('='*60 + '\n'))
