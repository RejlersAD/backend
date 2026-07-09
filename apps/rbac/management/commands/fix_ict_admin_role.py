"""
Django management command to fix ICT admin role assignment.
Removes super_admin from ICT department users and assigns admin role instead.

Usage:
    python manage.py fix_ict_admin_role
    python manage.py fix_ict_admin_role --email radai@rejlers.ae
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()


class Command(BaseCommand):
    help = 'Fix ICT admin role - remove super_admin and assign admin role'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Specific email to fix (default: radai@rejlers.ae)',
            default='radai@rejlers.ae'
        )

    def handle(self, *args, **options):
        email = options['email']
        
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(self.style.WARNING(f"ICT Admin Role Fix Script"))
        self.stdout.write(f"{'='*60}\n")
        
        try:
            # Get user
            user = User.objects.get(email=email)
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            
            self.stdout.write(f"📧 User: {user.email}")
            self.stdout.write(f"👤 Profile: {profile.full_name or user.email}")
            
            # Check current roles
            current_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
            self.stdout.write(f"\n📋 Current Roles:")
            for ur in current_roles:
                self.stdout.write(f"  • {ur.role.name} ({ur.role.code}) - Level {ur.role.level}")
            
            # Get roles
            try:
                super_admin_role = Role.objects.get(code='super_admin')
                admin_role = Role.objects.get(code='admin')
            except Role.DoesNotExist as e:
                self.stdout.write(self.style.ERROR(f"❌ Role not found: {e}"))
                return
            
            changes_made = False
            
            # Remove super_admin role if exists
            super_admin_assignments = UserRole.objects.filter(
                user_profile=profile,
                role=super_admin_role
            )
            if super_admin_assignments.exists():
                count = super_admin_assignments.delete()[0]
                self.stdout.write(self.style.SUCCESS(f"\n✅ Removed super_admin role from {email}"))
                changes_made = True
            else:
                self.stdout.write(self.style.WARNING(f"\n⚠️  User does not have super_admin role"))
            
            # Assign admin role if not exists
            admin_assignment, created = UserRole.objects.get_or_create(
                user_profile=profile,
                role=admin_role,
                defaults={
                    'is_primary': True,
                    'granted_by': user,
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"✅ Assigned admin role to {email}"))
                changes_made = True
            else:
                self.stdout.write(self.style.WARNING(f"⚠️  User already has admin role"))
            
            # Update User flags
            flag_changes = []
            if user.is_superuser:
                user.is_superuser = False
                flag_changes.append("is_superuser=False")
            if not user.is_staff:
                user.is_staff = True
                flag_changes.append("is_staff=True")
            
            if flag_changes:
                user.save()
                self.stdout.write(self.style.SUCCESS(f"✅ Updated User flags: {', '.join(flag_changes)}"))
                changes_made = True
            
            # Update department
            if profile.department != 'ICT':
                old_dept = profile.department
                profile.department = 'ICT'
                profile.save()
                self.stdout.write(self.style.SUCCESS(f"✅ Updated department: {old_dept} → ICT"))
                changes_made = True
            
            # Clear cache
            cache.delete(f'user_modules_{profile.id}')
            cache.delete(f'user_permissions_{profile.id}')
            self.stdout.write(self.style.SUCCESS(f"✅ Cleared module/permission cache"))
            
            # Show final roles
            final_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
            self.stdout.write(f"\n📋 Final Roles:")
            for ur in final_roles:
                self.stdout.write(self.style.SUCCESS(f"  • {ur.role.name} ({ur.role.code}) - Level {ur.role.level}"))
            
            self.stdout.write(f"\n{'='*60}")
            if changes_made:
                self.stdout.write(self.style.SUCCESS("✅ ICT admin role fix completed successfully!"))
                self.stdout.write(self.style.WARNING("\n⚠️  User must refresh browser or re-login to see changes"))
            else:
                self.stdout.write(self.style.WARNING("ℹ️  No changes needed - user already configured correctly"))
            self.stdout.write(f"{'='*60}\n")
            
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ User not found: {email}"))
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ UserProfile not found for: {email}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
