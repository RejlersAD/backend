"""
Advanced Duplicate Email Checker - Case Variations & Comprehensive Analysis
Smart detection of exact and case-variation duplicates
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from collections import defaultdict

User = get_user_model()

print('\n' + '='*90)
print('🔍 ADVANCED DUPLICATE EMAIL CHECKER')
print('='*90)

# Get all users
users = User.objects.all().select_related('rbac_profile')
total_users = users.count()

print(f'\n📊 Total Users: {total_users}')
print('='*90)

# ============================================================================
# 1. Check Exact Duplicates (case-sensitive)
# ============================================================================

print('\n1️⃣ EXACT DUPLICATES (Case-Sensitive)')
print('─'*90)

exact_email_map = defaultdict(list)
for user in users:
    exact_email_map[user.email].append(user)

exact_duplicates = {email: user_list for email, user_list in exact_email_map.items() if len(user_list) > 1}

if exact_duplicates:
    print(f'⚠️  Found {len(exact_duplicates)} exact duplicate email(s):\n')
    for email, user_list in exact_duplicates.items():
        print(f'   📧 {email} - {len(user_list)} accounts')
        for u in user_list:
            status = '✅ Active' if u.is_active else '❌ Inactive'
            try:
                profile = u.rbac_profile
                deleted = '🗑️ Deleted' if profile.is_deleted else ''
            except:
                deleted = ''
            print(f'      • ID: {u.id} | {u.first_name} {u.last_name} | {status} {deleted}')
else:
    print('✅ No exact duplicates found!')

# ============================================================================
# 2. Check Case Variations (case-insensitive)
# ============================================================================

print('\n2️⃣ CASE VARIATIONS (Case-Insensitive)')
print('─'*90)

normalized_email_map = defaultdict(list)
for user in users:
    normalized = user.email.lower()
    normalized_email_map[normalized].append(user)

case_variations = {
    email: user_list 
    for email, user_list in normalized_email_map.items() 
    if len(user_list) > 1 or len(set(u.email for u in user_list)) > 1
}

if case_variations:
    print(f'⚠️  Found {len(case_variations)} email(s) with case variations:\n')
    for normalized_email, user_list in case_variations.items():
        if len(user_list) > 1 or len(set(u.email for u in user_list)) > 1:
            print(f'   📧 Normalized: {normalized_email} - {len(user_list)} account(s)')
            email_variations = set(u.email for u in user_list)
            if len(email_variations) > 1:
                print(f'      Variations: {", ".join(email_variations)}')
            for u in user_list:
                status = '✅ Active' if u.is_active else '❌ Inactive'
                try:
                    profile = u.rbac_profile
                    deleted = '🗑️ Deleted' if profile.is_deleted else ''
                    roles = [r.name for r in profile.roles.all()]
                    role_str = f'| Roles: {", ".join(roles[:2])}' if roles else ''
                except:
                    deleted = ''
                    role_str = ''
                print(f'      • ID: {u.id} | {u.email} | {u.first_name} {u.last_name} | {status} {deleted} {role_str}')
            print()
else:
    print('✅ No case variations found!')

# ============================================================================
# 3. Summary Statistics
# ============================================================================

print('\n3️⃣ SUMMARY STATISTICS')
print('─'*90)

active_users = users.filter(is_active=True).count()
inactive_users = users.filter(is_active=False).count()
deleted_profiles = UserProfile.objects.filter(is_deleted=True).count()
superusers = users.filter(is_superuser=True).count()

print(f'Total Users: {total_users}')
print(f'  • Active: {active_users}')
print(f'  • Inactive: {inactive_users}')
print(f'  • Deleted Profiles: {deleted_profiles}')
print(f'  • Superusers: {superusers}')

# ============================================================================
# 4. Email Domain Analysis
# ============================================================================

print('\n4️⃣ EMAIL DOMAIN DISTRIBUTION')
print('─'*90)

domain_map = defaultdict(int)
for user in users:
    if '@' in user.email:
        domain = user.email.split('@')[1].lower()
        domain_map[domain] += 1

print('Top domains:')
for domain, count in sorted(domain_map.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f'  • {domain}: {count} users')

# ============================================================================
# 5. Recommendations
# ============================================================================

print('\n5️⃣ RECOMMENDATIONS')
print('─'*90)

issues = 0

if exact_duplicates:
    print(f'⚠️  {len(exact_duplicates)} exact duplicate(s) need resolution')
    issues += len(exact_duplicates)

if case_variations:
    actual_variations = sum(1 for user_list in case_variations.values() if len(set(u.email for u in user_list)) > 1)
    if actual_variations > 0:
        print(f'⚠️  {actual_variations} case variation(s) detected - may cause login issues')
        issues += actual_variations

if inactive_users > 0:
    print(f'ℹ️  {inactive_users} inactive user(s) could be cleaned up')

if deleted_profiles > 0:
    print(f'ℹ️  {deleted_profiles} soft-deleted profile(s) in database')

if issues == 0:
    print('✅ No critical issues found! Database is clean.')
else:
    print(f'\n⚠️  Total Issues: {issues}')

print('\n' + '='*90 + '\n')
