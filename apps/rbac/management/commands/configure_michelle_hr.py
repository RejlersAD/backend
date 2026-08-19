"""
Django Management Command: Configure Michelle's HR Access
Ensures michelle.dehoedt@rejlers.ae has HR module access
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()


class Command(BaseCommand):
    help = 'Configure HR access for michelle.dehoedt@rejlers.ae'

    def handle(self, *args, **options):
        # Soft-coded configuration
        TARGET_EMAIL = 'michelle.dehoedt@rejlers.ae'
        REQUIRED_ROLES = [
            {'code': 'default', 'is_primary': False},
            {'code': 'hr_admin', 'is_primary': True}
        ]
        
        self.stdout.write("="*80)
        self.stdout.write(self.style.SUCCESS(f"  CONFIGURING HR ACCESS FOR: {TARGET_EMAIL}"))
        self.stdout.write("="*80 + "\n")
        
        try:
            # Step 1: Find user
            user = User.objects.get(email=TARGET_EMAIL)
            self.stdout.write(self.style.SUCCESS(f"✓ User found: {user.email} (ID: {user.id})"))
            
            # Step 2: Get profile
            profile = UserProfile.objects.get(user=user)
            self.stdout.write(self.style.SUCCESS(f"✓ Profile found: {profile.id}"))
            
            # Step 3: Process each required role
            self.stdout.write(f"\n→ Processing {len(REQUIRED_ROLES)} required roles:\n")
            
            for role_config in REQUIRED_ROLES:
                role_code = role_config['code']
                is_primary = role_config['is_primary']
                
                try:
                    # Find role
                    role = Role.objects.get(code=role_code)
                    self.stdout.write(f"  → {role.name} (code: {role_code})")
                    
                    # Ensure role is active
                    if not role.is_active:
                        self.stdout.write(f"    ⚠ Role inactive - activating...")
                        role.is_active = True
                        role.save()
                        self.stdout.write(self.style.SUCCESS(f"    ✓ Activated"))
                    else:
                        self.stdout.write(f"    ✓ Active")
                    
                    # Check if already assigned
                    user_role, created = UserRole.objects.get_or_create(
                        user_profile=profile,
                        role=role,
                        defaults={'is_primary': is_primary}
                    )
                    
                    if created:
                        self.stdout.write(self.style.SUCCESS(f"    ✓ ASSIGNED (Primary: {is_primary})"))
                    else:
                        # Update primary flag if needed
                        if user_role.is_primary != is_primary:
                            user_role.is_primary = is_primary
                            user_role.save()
                            self.stdout.write(self.style.SUCCESS(f"    ✓ Updated primary flag to: {is_primary}"))
                        else:
                            self.stdout.write(f"    ✓ Already assigned (Primary: {is_primary})")
                    
                except Role.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f"  ✗ Role '{role_code}' not found"))
                    continue
            
            # Step 4: Clear cache
            try:
                from django.core.cache import cache
                cache.delete(f"user_permissions_{profile.id}")
                cache.delete(f"user_modules_{profile.id}")
                self.stdout.write(self.style.SUCCESS(f"\n✓ Cache cleared"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"\n⚠ Could not clear cache: {e}"))
            
            # Step 5: Show final state
            self.stdout.write("\n" + "="*80)
            self.stdout.write(self.style.SUCCESS("  FINAL STATE"))
            self.stdout.write("="*80 + "\n")
            
            final_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
            self.stdout.write(f"{TARGET_EMAIL} has {final_roles.count()} role(s):\n")
            
            for ur in final_roles:
                primary_flag = "⭐ PRIMARY" if ur.is_primary else "          "
                active_flag = "✓" if ur.role.is_active else "✗"
                self.stdout.write(f"  {primary_flag} | {active_flag} | {ur.role.name} (code: {ur.role.code})")
            
            # Step 6: Instructions
            self.stdout.write("\n" + "="*80)
            self.stdout.write(self.style.SUCCESS("  ✓ CONFIGURATION COMPLETE"))
            self.stdout.write("="*80)
            self.stdout.write("\n📌 Next Steps:")
            self.stdout.write(f"  1. Michelle must LOGOUT from https://www.radai.ae")
            self.stdout.write(f"  2. CLEAR browser cache (Ctrl+Shift+Del)")
            self.stdout.write(f"  3. LOGIN again")
            self.stdout.write(f"  4. HR modules should now be visible\n")
            self.stdout.write("="*80 + "\n")
            
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ User '{TARGET_EMAIL}' not found"))
            return
            
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ UserProfile for '{TARGET_EMAIL}' not found"))
            return
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Unexpected error: {e}"))
            import traceback
            traceback.print_exc()
            return
