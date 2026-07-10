#!/usr/bin/env python
"""
Fix Users Without Primary Role
Sets is_primary=True for users who have roles but no primary role set

Usage:
    docker exec aiflow_backend_local python _fix_missing_primary_roles.py [--dry-run]
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.rbac.models import UserProfile, UserRole
from django.db import transaction

# Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BOLD = '\033[1m'
RESET = '\033[0m'

def main():
    dry_run = '--dry-run' in sys.argv
    
    print(f"\n{BOLD}{'='*80}{RESET}")
    print(f"{BOLD}FIX MISSING PRIMARY ROLES{RESET}")
    print(f"{BOLD}{'='*80}{RESET}\n")
    
    if dry_run:
        print(f"{YELLOW}🔍 DRY RUN MODE - No changes will be saved{RESET}\n")
    
    # Find users without primary role
    users_fixed = []
    all_users = UserProfile.objects.filter(status='active', is_deleted=False)
    
    for profile in all_users:
        user_roles = UserRole.objects.filter(user_profile=profile)
        
        if user_roles.exists():
            has_primary = user_roles.filter(is_primary=True).exists()
            
            if not has_primary:
                # Set first role as primary
                first_role = user_roles.first()
                
                print(f"{YELLOW}⚠️  {profile.user.email}{RESET}")
                print(f"   Has {user_roles.count()} role(s) but no primary")
                print(f"   Setting as primary: {first_role.role.name} (code: {first_role.role.code})")
                
                if not dry_run:
                    with transaction.atomic():
                        first_role.is_primary = True
                        first_role.save()
                        print(f"   {GREEN}✅ Primary role set{RESET}")
                else:
                    print(f"   {YELLOW}[DRY RUN] Would set is_primary=True{RESET}")
                
                users_fixed.append(profile)
                print()
    
    # Summary
    print(f"\n{BOLD}{'='*80}{RESET}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"{BOLD}{'='*80}{RESET}")
    
    if users_fixed:
        if dry_run:
            print(f"{YELLOW}Would fix {len(users_fixed)} user(s) without primary role{RESET}")
        else:
            print(f"{GREEN}✅ Fixed {len(users_fixed)} user(s) without primary role{RESET}")
    else:
        print(f"{GREEN}✅ All users with roles have a primary role set{RESET}")
    
    if dry_run:
        print(f"\n{YELLOW}Run without --dry-run to apply changes{RESET}")
    
    print()

if __name__ == '__main__':
    main()
