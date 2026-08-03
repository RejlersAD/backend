"""
Django Management Command: Fix RBAC permissions for all users
Removes Django flags from non-admin users
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import models, transaction
from apps.rbac.models import UserProfile, UserRole, Role

User = get_user_model()

class Command(BaseCommand):
    help = 'Fix RBAC permissions by removing Django flags from non-admin users'
    
    # Soft-coded configuration
    AUTHORIZED_ADMIN_ROLES = ['super_admin', 'admin', 'ict_admin']
    PROTECTED_ADMINS = [
        'mohammed.agra@rejlers.ae',
        'fahad.hussein@rejlers.ae',
        'tanzeem.agra@rejlers.ae'
    ]
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them',
        )
        parser.add_argument(
            '--reactivate',
            action='store_true',
            help='Also reactivate fixed users',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        reactivate = options['reactivate']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  PRODUCTION RBAC FIX"))
        self.stdout.write("=" * 80)
        
        # Step 1: Find affected users (including inactive if --reactivate)
        self.stdout.write("\n[STEP 1] Finding affected users...")
        
        if reactivate:
            # Check ALL users (including inactive) when --reactivate is used
            affected_users = User.objects.filter(
                models.Q(is_superuser=True) | models.Q(is_staff=True)
            ).exclude(
                email__in=self.PROTECTED_ADMINS
            ).select_related('rbac_profile')
        else:
            # Check only active users when --reactivate is NOT used
            affected_users = User.objects.filter(
                models.Q(is_superuser=True) | models.Q(is_staff=True),
                is_active=True
            ).exclude(
                email__in=self.PROTECTED_ADMINS
            ).select_related('rbac_profile')
        
        affected_list = []
        for user in affected_users:
            try:
                profile = UserProfile.objects.get(user=user, is_deleted=False)
                roles = UserRole.objects.filter(
                    user_profile=profile,
                    role__is_active=True
                ).select_related('role')
                
                role_codes = [ur.role.code for ur in roles]
                has_admin = any(c in self.AUTHORIZED_ADMIN_ROLES for c in role_codes)
                
                if not has_admin:
                    affected_list.append({
                        'user': user,
                        'email': user.email,
                        'is_active': user.is_active,
                        'is_superuser': user.is_superuser,
                        'is_staff': user.is_staff,
                        'roles': ', '.join(role_codes),
                    })
            except:
                pass
        
        self.stdout.write(self.style.WARNING(f"   Found {len(affected_list)} users"))
        
        if not affected_list:
            self.stdout.write(self.style.SUCCESS("\n✅ No users need fixing!"))
            return
        
        # Show sample
        self.stdout.write("\n   Sample users:")
        for item in affected_list[:10]:
            active_str = "active" if item['is_active'] else "INACTIVE"
            flags = f"super={item['is_superuser']}, staff={item['is_staff']}"
            self.stdout.write(f"   - {item['email']} ({active_str}, {flags}, role={item['roles']})")
        
        if len(affected_list) > 10:
            self.stdout.write(f"   ... and {len(affected_list) - 10} more")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n🔍 DRY RUN - No changes made"))
            self.stdout.write(f"   To apply fix, run without --dry-run flag")
            return
        
        # Step 2: Fix flags
        self.stdout.write("\n[STEP 2] Removing Django flags...")
        
        fixed_count = 0
        with transaction.atomic():
            for item in affected_list:
                user = item['user']
                user.is_superuser = False
                user.is_staff = False
                user.save(update_fields=['is_superuser', 'is_staff'])
                fixed_count += 1
        
        self.stdout.write(self.style.SUCCESS(f"   ✅ Fixed {fixed_count} users"))
        
        # Step 3: Reactivate
        if reactivate:
            self.stdout.write("\n[STEP 3] Reactivating users...")
            reactivated = 0
            with transaction.atomic():
                for item in affected_list:
                    if not item['is_active']:
                        user = item['user']
                        user.is_active = True
                        user.save(update_fields=['is_active'])
                        reactivated += 1
            
            self.stdout.write(self.style.SUCCESS(f"   ✅ Reactivated {reactivated} users"))
        
        # Step 4: Verify
        self.stdout.write("\n[STEP 4] Verifying...")
        
        remaining = User.objects.filter(
            models.Q(is_superuser=True) | models.Q(is_staff=True),
            is_active=True
        ).exclude(
            email__in=self.PROTECTED_ADMINS
        )
        
        remaining_count = 0
        for user in remaining:
            try:
                profile = UserProfile.objects.get(user=user, is_deleted=False)
                roles = UserRole.objects.filter(
                    user_profile=profile,
                    role__is_active=True
                ).select_related('role')
                
                role_codes = [ur.role.code for ur in roles]
                has_admin = any(c in self.AUTHORIZED_ADMIN_ROLES for c in role_codes)
                
                if not has_admin:
                    remaining_count += 1
            except:
                pass
        
        self.stdout.write(f"   Remaining issues: {remaining_count}")
        
        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("  COMPLETE"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"  Fixed:     {fixed_count} users")
        if reactivate:
            self.stdout.write(f"  Reactivated: (see above)")
        self.stdout.write(f"  Remaining: {remaining_count} issues")
        self.stdout.write("=" * 80)
        
        if remaining_count == 0:
            self.stdout.write(self.style.SUCCESS("\n✅ SUCCESS!"))
            self.stdout.write("\n📋 Users should logout and login")
            self.stdout.write("   Access now controlled by RBAC only")
        else:
            self.stdout.write(self.style.WARNING(f"\n⚠️  {remaining_count} issues remain"))
