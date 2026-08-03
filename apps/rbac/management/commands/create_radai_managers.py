"""
Django Management Command: Create RadAI Department Managers
===========================================================
Creates three missing managers for the Reporting Manager dropdown.

Usage:
    python manage.py create_radai_managers
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization

User = get_user_model()


class Command(BaseCommand):
    help = 'Create three RadAI managers for the Reporting Manager dropdown'

    def handle(self, *args, **options):
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("🔧 CREATE RADAI MANAGERS"))
        self.stdout.write("="*80 + "\n")

        # Get or create organization
        org = Organization.objects.filter(is_active=True).order_by('created_at').first()
        
        if not org:
            self.stdout.write(self.style.ERROR("❌ No active organization found!"))
            self.stdout.write("   Create an organization first.")
            return

        self.stdout.write(f"📍 Using organization: {org.name} ({org.id})\n")

        # Managers to create
        managers = [
            {
                'email': 'rafat.sm.saqer@rejlers.ae',
                'first_name': 'Rafat',
                'last_name': 'S. M. Saqer',
                'username': 'rafat_sm_saqer',
            },
            {
                'email': 'anam.abbas@rejlers.ae',
                'first_name': 'Anam',
                'last_name': 'Abbas',
                'username': 'anam_abbas',
            },
            {
                'email': 'aleksi.murtomaki@rejlers.ae',
                'first_name': 'Aleksi',
                'last_name': 'Murtomaki',
                'username': 'aleksi_murtomaki',
            },
        ]

        created_count = 0
        updated_count = 0

        self.stdout.write("👤 CREATING/UPDATING MANAGERS")
        self.stdout.write("-" * 80)

        for mgr in managers:
            email = mgr['email']
            
            # Create or update user
            user, user_created = User.objects.update_or_create(
                email=email,
                defaults={
                    'username': mgr['username'],
                    'first_name': mgr['first_name'],
                    'last_name': mgr['last_name'],
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                }
            )

            if user_created:
                # Set unusable password for new users
                user.set_unusable_password()
                user.save()
                self.stdout.write(f"  ✅ Created user: {mgr['first_name']} {mgr['last_name']}")
            else:
                self.stdout.write(f"  ✅ Updated user: {mgr['first_name']} {mgr['last_name']}")

            # Create or update profile
            profile, profile_created = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'organization': org,
                    'department': 'radai',
                    'job_title': 'Manager',
                    'status': 'active',
                    'is_deleted': False,
                }
            )

            if profile_created:
                self.stdout.write(f"     ✅ Created profile: department=radai, status=active")
                created_count += 1
            else:
                self.stdout.write(f"     🔄 Updated profile: department=radai, status=active")
                updated_count += 1

        # Verification
        self.stdout.write("\n" + "-" * 80)
        self.stdout.write("🔍 VERIFICATION")
        self.stdout.write("-" * 80)

        for mgr in managers:
            try:
                user = User.objects.get(email=mgr['email'])
                profile = UserProfile.objects.get(user=user)
                
                # Check visibility criteria
                is_visible = (
                    user.is_active and 
                    profile.status == 'active' and 
                    not profile.is_deleted and
                    profile.organization_id is not None
                )
                
                status_icon = "✅" if is_visible else "❌"
                self.stdout.write(
                    f"  {status_icon} {user.get_full_name()} ({user.email})"
                )
                self.stdout.write(
                    f"     Organization: {profile.organization.name}"
                )
                self.stdout.write(
                    f"     Department: {profile.department} | Job Title: {profile.job_title}"
                )
                self.stdout.write(
                    f"     Status: {profile.status} | Active: {user.is_active} | Deleted: {profile.is_deleted}"
                )
                
                if not is_visible:
                    self.stdout.write(
                        self.style.WARNING(f"     ⚠️  This user will NOT appear in dropdown!")
                    )
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  ❌ {mgr['email']}: {str(e)}")
                )

        # Summary
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("✅ COMPLETION SUMMARY"))
        self.stdout.write("="*80)
        self.stdout.write(f"Profiles created:    {created_count}")
        self.stdout.write(f"Profiles updated:    {updated_count}")
        self.stdout.write(f"Total managers:      {len(managers)}")
        self.stdout.write(f"Organization:        {org.name}")
        self.stdout.write("="*80 + "\n")

        self.stdout.write(
            self.style.SUCCESS("🎉 SUCCESS! Managers are now available in Profile dropdown!")
        )
        self.stdout.write("   Go to https://www.radai.ae/profile and check 'Reporting Manager' field")
        self.stdout.write("   You may need to clear browser cache (Ctrl+Shift+R)\n")
