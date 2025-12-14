"""
Management command to assign super admin role to a user
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, UserProfile, UserRole

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
        
        self.stdout.write(self.style.WARNING('Assigning Super Admin roles...'))
        
        try:
            # Get super admin role
            super_admin_role = Role.objects.get(code='super_admin')
            self.stdout.write(f'Found Super Admin role: {super_admin_role.name}')
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('Super Admin role not found! Run seed_rbac first.'))
            return
        
        for user_email in emails_to_process:
            try:
                # Get user
                user = User.objects.get(email=user_email)
                
                # Get or create user profile
                profile, created = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'organization': UserProfile.objects.filter(
                            organization__isnull=False
                        ).first().organization if UserProfile.objects.filter(
                            organization__isnull=False
                        ).exists() else None,
                        'status': 'active'
                    }
                )
                
                if created:
                    self.stdout.write(f'  Created profile for: {user_email}')
                
                # Ensure profile is active
                if profile.status != 'active':
                    profile.status = 'active'
                    profile.save()
                    self.stdout.write(f'  Activated profile for: {user_email}')
                
                # Assign super admin role
                user_role, role_created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=super_admin_role,
                    defaults={'is_primary': True}
                )
                
                if role_created:
                    self.stdout.write(self.style.SUCCESS(
                        f'✓ Assigned Super Admin role to: {user_email}'
                    ))
                else:
                    self.stdout.write(f'  {user_email} already has Super Admin role')
                
            except User.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'✗ User not found: {user_email}'
                ))
                continue
        
        self.stdout.write(self.style.SUCCESS('\n✅ Super Admin assignment completed!'))
