"""
Smart User Database Synchronization Diagnostic Tool
Analyzes user data integrity and synchronization issues
"""

import os
import sys
import django
from datetime import datetime

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()

class UserSyncDiagnostic:
    def __init__(self):
        self.issues = []
        
    def print_section(self, title):
        print(f"\n{'='*90}")
        print(f"  {title}")
        print(f"{'='*90}")
    
    def print_subsection(self, title):
        print(f"\n{title}")
        print(f"{'-'*90}")
    
    def analyze_user_counts(self):
        """Analyze user counts in different states"""
        self.print_section("📊 USER COUNT ANALYSIS")
        
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        inactive_users = User.objects.filter(is_active=False).count()
        superusers = User.objects.filter(is_superuser=True).count()
        verified_users = User.objects.filter(is_verified=True).count()
        
        # Users with deleted emails
        deleted_email_users = User.objects.filter(email__contains='.deleted_').count()
        
        # Users without RBAC profile
        users_without_profile = User.objects.filter(rbac_profile__isnull=True).count()
        
        print(f"\n📈 Database Statistics:")
        print(f"   Total Users in Database:        {total_users}")
        print(f"   ✅ Active Users:                 {active_users}")
        print(f"   ❌ Inactive Users:                {inactive_users}")
        print(f"   👑 Superusers:                    {superusers}")
        print(f"   ✓  Verified Users:                {verified_users}")
        print(f"   🗑️  Soft-Deleted (*.deleted_*):   {deleted_email_users}")
        print(f"   ⚠️  Users without RBAC Profile:   {users_without_profile}")
        
        # Expected active users for admin UI
        expected_admin_ui = active_users - deleted_email_users
        print(f"\n🎯 Expected Admin UI Count:       {expected_admin_ui}")
        print(f"   (Active users - Soft-deleted users)")
        
        if users_without_profile > 0:
            self.issues.append(f"{users_without_profile} users don't have RBAC profiles")
        
        return {
            'total': total_users,
            'active': active_users,
            'inactive': inactive_users,
            'deleted': deleted_email_users,
            'without_profile': users_without_profile,
            'expected_admin_ui': expected_admin_ui
        }
    
    def analyze_rbac_profiles(self):
        """Analyze RBAC profile consistency"""
        self.print_section("🔐 RBAC PROFILE ANALYSIS")
        
        total_profiles = UserProfile.objects.count()
        profiles_with_roles = UserProfile.objects.filter(roles__isnull=False).distinct().count()
        profiles_without_roles = total_profiles - profiles_with_roles
        
        # Users with profile but user is inactive
        inactive_user_profiles = UserProfile.objects.filter(user__is_active=False).count()
        
        # Active users with profiles
        active_user_profiles = UserProfile.objects.filter(user__is_active=True).count()
        
        print(f"\n📊 RBAC Profile Statistics:")
        print(f"   Total RBAC Profiles:              {total_profiles}")
        print(f"   ✅ Active User Profiles:          {active_user_profiles}")
        print(f"   ❌ Inactive User Profiles:        {inactive_user_profiles}")
        print(f"   🎭 Profiles with Roles:           {profiles_with_roles}")
        print(f"   ⚠️  Profiles without Roles:        {profiles_without_roles}")
        
        if profiles_without_roles > 0:
            self.issues.append(f"{profiles_without_roles} RBAC profiles have no roles assigned")
        
        return {
            'total_profiles': total_profiles,
            'active_profiles': active_user_profiles,
            'profiles_with_roles': profiles_with_roles
        }
    
    def find_orphaned_records(self):
        """Find orphaned or problematic records"""
        self.print_section("🔍 ORPHANED RECORDS CHECK")
        
        problems = []
        
        # Users without RBAC profiles
        users_no_profile = User.objects.filter(rbac_profile__isnull=True, is_active=True)
        if users_no_profile.exists():
            problems.append({
                'type': 'Missing RBAC Profile',
                'count': users_no_profile.count(),
                'users': list(users_no_profile.values_list('id', 'email', 'username'))[:10]
            })
        
        # RBAC profiles with deleted users
        profiles_deleted_users = UserProfile.objects.filter(user__email__contains='.deleted_')
        if profiles_deleted_users.exists():
            problems.append({
                'type': 'RBAC Profile for Deleted User',
                'count': profiles_deleted_users.count(),
                'users': list(profiles_deleted_users.values_list('user__id', 'user__email'))[:10]
            })
        
        # Duplicate email check (shouldn't exist after cleanup)
        from django.db.models import Count
        duplicate_emails = User.objects.values('email').annotate(
            count=Count('id')
        ).filter(count__gt=1)
        
        if duplicate_emails.exists():
            problems.append({
                'type': 'Duplicate Emails Still Exist',
                'count': duplicate_emails.count(),
                'emails': list(duplicate_emails.values_list('email', 'count'))[:10]
            })
        
        if problems:
            print(f"\n⚠️  Found {len(problems)} types of issues:\n")
            for i, problem in enumerate(problems, 1):
                print(f"{i}. {problem['type']}: {problem['count']} records")
                if problem['count'] > 0 and problem['count'] <= 10:
                    for record in problem['users'] if 'users' in problem else problem.get('emails', []):
                        print(f"   - {record}")
                elif problem['count'] > 10:
                    print(f"   (Showing first 10 of {problem['count']} records)")
                    for record in (problem['users'] if 'users' in problem else problem.get('emails', []))[:10]:
                        print(f"   - {record}")
                print()
            
            for problem in problems:
                self.issues.append(f"{problem['type']}: {problem['count']} records")
        else:
            print(f"\n✅ No orphaned records found!")
        
        return problems
    
    def analyze_role_distribution(self):
        """Analyze role distribution"""
        self.print_section("🎭 ROLE DISTRIBUTION ANALYSIS")
        
        total_roles = Role.objects.count()
        
        # Count users per role
        print(f"\n📊 Role Assignment Statistics:")
        print(f"   Total Roles in System: {total_roles}\n")
        
        role_stats = []
        for role in Role.objects.all().order_by('name'):
            user_count = role.user_profiles.filter(user__is_active=True).count()
            total_count = role.user_profiles.count()
            role_stats.append({
                'name': role.name,
                'code': role.code,
                'active_users': user_count,
                'total_users': total_count
            })
            print(f"   🎭 {role.name:40s} | Active: {user_count:3d} | Total: {total_count:3d}")
        
        return role_stats
    
    def check_admin_ui_query(self):
        """Check what the admin UI should be querying"""
        self.print_section("🖥️  ADMIN UI QUERY SIMULATION")
        
        # Simulate typical admin UI query
        admin_users = User.objects.filter(
            is_active=True
        ).exclude(
            email__contains='.deleted_'
        ).order_by('-date_joined')
        
        admin_count = admin_users.count()
        
        print(f"\n🔍 Admin UI Query Results:")
        print(f"   Filter: is_active=True AND NOT email LIKE '%.deleted_%'")
        print(f"   Result Count: {admin_count} users")
        print(f"\n   First 10 users:")
        for i, user in enumerate(admin_users[:10], 1):
            status = "👑 Superuser" if user.is_superuser else "👤 User"
            print(f"   {i:2d}. {status} | {user.email:40s} | {user.get_full_name() or user.username}")
        
        return admin_count
    
    def generate_cleanup_recommendations(self):
        """Generate cleanup recommendations"""
        self.print_section("💡 RECOMMENDATIONS")
        
        if not self.issues:
            print(f"\n✅ Database is in good shape! No major issues found.")
            print(f"\n📝 Notes:")
            print(f"   - The difference between Railway DB count (357) and Admin UI (325)")
            print(f"   - is due to soft-deleted duplicate accounts (marked inactive)")
            print(f"   - This is EXPECTED behavior after the duplicate cleanup")
            print(f"   - The RBAC system should work correctly with active users")
        else:
            print(f"\n⚠️  Found {len(self.issues)} issue(s) requiring attention:\n")
            for i, issue in enumerate(self.issues, 1):
                print(f"   {i}. {issue}")
            
            print(f"\n🔧 Recommended Actions:")
            
            if any('RBAC profile' in issue.lower() for issue in self.issues):
                print(f"\n   1. Create RBAC profiles for users without them:")
                print(f"      python create_missing_rbac_profiles.py")
            
            if any('without roles' in issue.lower() for issue in self.issues):
                print(f"\n   2. Review users without roles and assign appropriate access:")
                print(f"      python assign_default_roles.py")
            
            if any('deleted user' in issue.lower() for issue in self.issues):
                print(f"\n   3. Clean up RBAC profiles for soft-deleted users:")
                print(f"      python cleanup_deleted_user_profiles.py")
        
        return self.issues
    
    def run(self):
        """Run full diagnostic"""
        print(f"\n{'#'*90}")
        print(f"#{'SMART USER DATABASE SYNCHRONIZATION DIAGNOSTIC'.center(88)}#")
        print(f"#{'Railway PostgreSQL → Local Admin UI'.center(88)}#")
        print(f"{'#'*90}")
        print(f"\n🕐 Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Run all analyses
        counts = self.analyze_user_counts()
        profiles = self.analyze_rbac_profiles()
        orphans = self.find_orphaned_records()
        roles = self.analyze_role_distribution()
        admin_count = self.check_admin_ui_query()
        
        # Generate recommendations
        recommendations = self.generate_cleanup_recommendations()
        
        # Final summary
        self.print_section("📋 SUMMARY")
        
        print(f"\n🎯 Database vs Admin UI Reconciliation:")
        print(f"   Railway Database Total:    357 users (as reported)")
        print(f"   Database Query Total:      {counts['total']} users")
        print(f"   Active Users:              {counts['active']} users")
        print(f"   Soft-Deleted:              {counts['deleted']} users")
        print(f"   Expected Admin UI:         {counts['expected_admin_ui']} users")
        print(f"   Actual Admin UI:           325 users (as reported)")
        print(f"   Simulated Admin UI Query:  {admin_count} users")
        
        discrepancy = counts['expected_admin_ui'] - 325
        if abs(discrepancy) <= 5:
            print(f"\n✅ Counts are consistent! Difference of {abs(discrepancy)} is acceptable.")
            print(f"   (May be due to recent updates or cache)")
        else:
            print(f"\n⚠️  Discrepancy detected: {discrepancy} users")
            print(f"   Further investigation needed.")
        
        print(f"\n{'='*90}\n")


if __name__ == "__main__":
    diagnostic = UserSyncDiagnostic()
    diagnostic.run()
