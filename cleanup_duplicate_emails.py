"""
Smart Duplicate Email Cleanup Script
Processes duplicate email addresses one by one with detailed logging and safety checks
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
from django.db import transaction
from apps.rbac.models import UserProfile, Role

User = get_user_model()

class DuplicateEmailCleaner:
    def __init__(self):
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.log_file = f"duplicate_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    def log(self, message):
        """Log to both console and file"""
        print(message)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    
    def get_duplicate_groups(self):
        """Find all email groups with case variations"""
        all_users = User.objects.all()
        email_groups = {}
        
        for user in all_users:
            normalized = user.email.lower()
            if normalized not in email_groups:
                email_groups[normalized] = []
            email_groups[normalized].append(user)
        
        # Return only groups with duplicates
        return {k: v for k, v in email_groups.items() if len(v) > 1}
    
    def select_primary_account(self, users):
        """
        Smart selection of primary account to keep
        Priority:
        1. Active + Has roles + Most recent login
        2. Active + Is superuser
        3. Active + Has roles
        4. Active account
        5. Most recent account
        """
        def score_user(user):
            score = 0
            
            # Active users get priority
            if user.is_active:
                score += 1000
            
            # Superusers get high priority
            if user.is_superuser:
                score += 500
            
            # Users with roles get priority
            try:
                if hasattr(user, 'rbac_profile') and user.rbac_profile:
                    role_count = user.rbac_profile.module_roles.count()
                    score += role_count * 10
            except:
                pass
            
            # Recent login gets priority
            if user.last_login:
                days_since_login = (datetime.now().date() - user.last_login.date()).days
                score += max(0, 100 - days_since_login)
            
            # Recent creation gets small priority
            if user.date_joined:
                days_since_creation = (datetime.now().date() - user.date_joined.date()).days
                score += max(0, 50 - days_since_creation)
            
            # Lower ID (older account) gets small priority
            score += (10000 - user.id) * 0.01
            
            return score
        
        users_with_scores = [(user, score_user(user)) for user in users]
        users_with_scores.sort(key=lambda x: x[1], reverse=True)
        
        return users_with_scores[0][0], users_with_scores
    
    def get_user_info(self, user):
        """Get detailed user information"""
        try:
            roles = []
            if hasattr(user, 'rbac_profile') and user.rbac_profile:
                roles = [role.name for role in user.rbac_profile.roles.all()]
            
            status = "✅ Active" if user.is_active else "❌ Inactive"
            if user.is_superuser:
                status += " 👑 Superuser"
            
            return {
                'id': user.id,
                'email': user.email,
                'full_name': user.get_full_name() or user.username,
                'status': status,
                'roles': roles,
                'role_count': len(roles),
                'last_login': user.last_login.strftime('%Y-%m-%d') if user.last_login else 'Never',
                'created': user.date_joined.strftime('%Y-%m-%d') if user.date_joined else 'Unknown'
            }
        except Exception as e:
            return {
                'id': user.id,
                'email': user.email,
                'error': str(e)
            }
    
    def consolidate_roles(self, primary_user, duplicate_users):
        """Consolidate roles from duplicate accounts to primary account"""
        try:
            if not hasattr(primary_user, 'rbac_profile') or not primary_user.rbac_profile:
                return 0
            
            primary_profile = primary_user.rbac_profile
            consolidated_count = 0
            
            for dup_user in duplicate_users:
                try:
                    if hasattr(dup_user, 'rbac_profile') and dup_user.rbac_profile:
                        dup_roles = dup_user.rbac_profile.roles.all()
                        for role in dup_roles:
                            # Add role to primary if not already present
                            if not primary_profile.roles.filter(id=role.id).exists():
                                primary_profile.roles.add(role)
                                consolidated_count += 1
                except Exception as e:
                    self.log(f"      ⚠️  Error consolidating roles from user {dup_user.id}: {str(e)}")
            
            return consolidated_count
        except Exception as e:
            self.log(f"      ⚠️  Error in consolidate_roles: {str(e)}")
            return 0
    
    def soft_delete_duplicate(self, user):
        """Soft delete a duplicate user account by deactivating it"""
        try:
            user.is_active = False
            # Append suffix to email to prevent unique constraint issues
            user.email = f"{user.email}.deleted_{user.id}"
            user.save()
            return True
        except Exception as e:
            self.log(f"      ❌ Error soft-deleting user {user.id}: {str(e)}")
            return False
    
    def process_duplicate_group(self, normalized_email, users, group_number, total_groups):
        """Process one group of duplicate emails"""
        self.log(f"\n{'='*90}")
        self.log(f"[{group_number}/{total_groups}] Processing: {normalized_email}")
        self.log(f"{'='*90}")
        
        # Show all accounts in this group
        self.log(f"\n📧 Found {len(users)} accounts:")
        for i, user in enumerate(users, 1):
            info = self.get_user_info(user)
            if 'error' in info:
                self.log(f"   {i}. ID: {info['id']} | {info['email']} | ⚠️  Error: {info['error']}")
                continue
            self.log(f"   {i}. ID: {info['id']:3d} | {info['email']:40s} | {info['status']:30s}")
            self.log(f"      Name: {info['full_name']}")
            self.log(f"      Roles: {', '.join(info['roles']) if info['roles'] else 'None'}")
            self.log(f"      Last Login: {info['last_login']} | Created: {info['created']}")
        
        # Select primary account
        primary_user, scored_users = self.select_primary_account(users)
        duplicate_users = [u for u in users if u.id != primary_user.id]
        
        self.log(f"\n🎯 Selected Primary Account (Highest Score):")
        primary_info = self.get_user_info(primary_user)
        self.log(f"   ID: {primary_info['id']} | {primary_info['email']} | Score: {scored_users[0][1]:.2f}")
        
        if len(duplicate_users) > 0:
            self.log(f"\n🗑️  Accounts to be soft-deleted ({len(duplicate_users)}):")
            for user, score in scored_users[1:]:
                info = self.get_user_info(user)
                self.log(f"   ID: {info['id']} | {info['email']} | Score: {score:.2f}")
        
        # Ask for confirmation
        self.log(f"\n⚠️  CONFIRMATION REQUIRED")
        self.log(f"   Keep: ID {primary_user.id} - {primary_user.email}")
        self.log(f"   Delete: {len(duplicate_users)} duplicate(s)")
        
        response = input(f"\n   Proceed? (y/n/s=skip): ").strip().lower()
        
        if response == 's':
            self.log(f"   ⏭️  SKIPPED by user\n")
            self.skipped += 1
            return False
        
        if response != 'y':
            self.log(f"   ❌ CANCELLED by user\n")
            self.skipped += 1
            return False
        
        # Execute cleanup in transaction
        try:
            with transaction.atomic():
                # Consolidate roles first
                self.log(f"\n🔄 Consolidating roles...")
                consolidated = self.consolidate_roles(primary_user, duplicate_users)
                self.log(f"   ✅ Consolidated {consolidated} role(s) to primary account")
                
                # Soft delete duplicates
                self.log(f"\n🗑️  Soft-deleting duplicates...")
                for dup_user in duplicate_users:
                    if self.soft_delete_duplicate(dup_user):
                        self.log(f"   ✅ Soft-deleted ID {dup_user.id} - {dup_user.email}")
                    else:
                        raise Exception(f"Failed to soft-delete user {dup_user.id}")
                
                self.log(f"\n✅ SUCCESS - Cleaned up {len(duplicate_users)} duplicate(s)")
                self.processed += 1
                return True
                
        except Exception as e:
            self.log(f"\n❌ ERROR: {str(e)}")
            self.log(f"   Transaction rolled back - no changes made")
            self.errors += 1
            return False
    
    def run(self):
        """Main execution method"""
        self.log(f"\n{'='*90}")
        self.log(f"SMART DUPLICATE EMAIL CLEANUP - ONE BY ONE")
        self.log(f"{'='*90}")
        self.log(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"Log file: {self.log_file}")
        
        # Get all duplicate groups
        self.log(f"\n🔍 Scanning for duplicate emails...")
        duplicate_groups = self.get_duplicate_groups()
        total_groups = len(duplicate_groups)
        total_duplicates = sum(len(users) - 1 for users in duplicate_groups.values())
        
        if total_groups == 0:
            self.log(f"\n✅ No duplicate emails found!")
            return
        
        self.log(f"\n📊 Found {total_groups} email(s) with duplicates")
        self.log(f"   Total duplicate accounts to process: {total_duplicates}")
        
        # Process each group one by one
        for idx, (normalized_email, users) in enumerate(duplicate_groups.items(), 1):
            self.process_duplicate_group(normalized_email, users, idx, total_groups)
            
            # Show progress
            if idx < total_groups:
                self.log(f"\n{'─'*90}")
                self.log(f"Progress: {idx}/{total_groups} groups processed")
                input("\nPress Enter to continue to next duplicate group...")
        
        # Final summary
        self.log(f"\n{'='*90}")
        self.log(f"CLEANUP SUMMARY")
        self.log(f"{'='*90}")
        self.log(f"Total duplicate groups: {total_groups}")
        self.log(f"✅ Successfully processed: {self.processed}")
        self.log(f"⏭️  Skipped: {self.skipped}")
        self.log(f"❌ Errors: {self.errors}")
        self.log(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"Full log saved to: {self.log_file}")


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════════════════════╗
║                    SMART DUPLICATE EMAIL CLEANUP TOOL                            ║
║                                                                                  ║
║  This script will:                                                               ║
║  • Identify all email addresses with case variations                             ║
║  • Intelligently select the best account to keep (primary)                       ║
║  • Consolidate roles from duplicates to the primary account                      ║
║  • Soft-delete duplicate accounts (can be recovered)                             ║
║  • Process ONE duplicate group at a time with your confirmation                  ║
║                                                                                  ║
║  ⚠️  IMPORTANT: You will be asked to confirm EACH duplicate group individually   ║
╚══════════════════════════════════════════════════════════════════════════════════╝
    """)
    
    response = input("\n🚀 Ready to start? (yes/no): ").strip().lower()
    if response in ['yes', 'y']:
        cleaner = DuplicateEmailCleaner()
        cleaner.run()
    else:
        print("\n❌ Operation cancelled by user")
