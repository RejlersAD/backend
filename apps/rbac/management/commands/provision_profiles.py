"""
Management command to provision UserProfiles for all users
==========================================================
This command ensures all users have a UserProfile record.

Usage:
    python manage.py provision_profiles
    
    # Or on Railway:
    railway run python manage.py provision_profiles
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile, Organization

User = get_user_model()


class Command(BaseCommand):
    help = 'Provision UserProfiles for all users who don\'t have one'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('User Profile Provisioning'))
        self.stdout.write(self.style.WARNING('=' * 70))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 DRY RUN MODE - No changes will be made\n'))
        
        # Get or create default organization
        try:
            organization = Organization.objects.filter(is_active=True).first()
            
            if not organization:
                self.stdout.write(self.style.WARNING('No active organization found. Creating default...'))
                
                if not dry_run:
                    with transaction.atomic():
                        organization, created = Organization.objects.get_or_create(
                            code='DEFAULT_ORG',
                            defaults={
                                'name': 'Default Organization',
                                'description': 'Auto-created default organization for profile provisioning',
                                'is_active': True,
                            }
                        )
                    if created:
                        self.stdout.write(self.style.SUCCESS(f'✅ Created organization: {organization.name}'))
                else:
                    self.stdout.write(self.style.WARNING('   → Would create default organization'))
                    organization = None
            else:
                self.stdout.write(self.style.SUCCESS(f'✅ Using organization: {organization.name} (ID: {organization.id})'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error getting organization: {e}'))
            return
        
        # Get users without profiles
        users_without_profile = User.objects.filter(rbac_profile__isnull=True)
        total_users = User.objects.count()
        users_with_profile = total_users - users_without_profile.count()
        
        self.stdout.write(f'\n📊 User Statistics:')
        self.stdout.write(f'   Total users: {total_users}')
        self.stdout.write(f'   Users with profiles: {users_with_profile}')
        self.stdout.write(f'   Users without profiles: {users_without_profile.count()}')
        
        if users_without_profile.count() == 0:
            self.stdout.write(self.style.SUCCESS('\n✅ All users already have profiles!'))
            return
        
        # Provision profiles
        self.stdout.write(f'\n🔧 Provisioning profiles for {users_without_profile.count()} users...\n')
        
        success_count = 0
        error_count = 0
        
        for user in users_without_profile:
            try:
                if not dry_run:
                    with transaction.atomic():
                        profile = UserProfile.objects.create(
                            user=user,
                            organization=organization,
                            bio='',
                            job_title=user.username or (user.email or '').split('@')[0],
                        )
                    self.stdout.write(
                        self.style.SUCCESS(f'   ✅ Created profile for: {user.email} (Profile ID: {profile.id})')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f'   → Would create profile for: {user.email}')
                    )
                success_count += 1
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'   ❌ Failed to create profile for {user.email}: {e}')
                )
                error_count += 1
        
        # Summary
        self.stdout.write(self.style.WARNING('\n' + '=' * 70))
        self.stdout.write(self.style.WARNING('Summary'))
        self.stdout.write(self.style.WARNING('=' * 70))
        
        if dry_run:
            self.stdout.write(f'Would provision: {success_count} profiles')
        else:
            self.stdout.write(self.style.SUCCESS(f'✅ Successfully provisioned: {success_count} profiles'))
            if error_count > 0:
                self.stdout.write(self.style.ERROR(f'❌ Failed: {error_count} profiles'))
        
        # Verify
        if not dry_run:
            users_without_profile_after = User.objects.filter(rbac_profile__isnull=True).count()
            if users_without_profile_after == 0:
                self.stdout.write(self.style.SUCCESS('\n✅ All users now have profiles!'))
            else:
                self.stdout.write(self.style.WARNING(f'\n⚠️  {users_without_profile_after} users still without profiles'))
        
        self.stdout.write('')
