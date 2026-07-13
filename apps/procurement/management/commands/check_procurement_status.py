"""
Django Management Command: Check Procurement Module Status
Smart diagnostic command to verify procurement data, migrations, and configuration
Uses soft-coded configuration for environment detection
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor, Receipt
from django.contrib.auth import get_user_model
import os

User = get_user_model()


class Command(BaseCommand):
    help = 'Check procurement module status: data counts, migrations, and configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information including sample records',
        )
        parser.add_argument(
            '--environment',
            type=str,
            default='auto',
            help='Specify environment (auto, local, production, staging)',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        environment = options['environment']
        
        # Detect environment from configuration
        env_name = self._detect_environment(environment)
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"  PROCUREMENT MODULE STATUS CHECK"))
        self.stdout.write("=" * 80)
        
        # 1. Environment Info
        self._show_environment_info(env_name)
        
        # 2. Data Counts
        self._show_data_counts()
        
        # 3. Migration Status
        self._show_migration_status()
        
        # 4. Sample Data (if verbose)
        if verbose:
            self._show_sample_data()
        
        # 5. Health Check
        self._show_health_check()
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("✓ Status check complete"))
        self.stdout.write("=" * 80)

    def _detect_environment(self, env_arg):
        """Soft-coded environment detection"""
        if env_arg != 'auto':
            return env_arg
        
        # Check environment variable
        env_from_var = os.environ.get('ENVIRONMENT', '').lower()
        if env_from_var:
            return env_from_var
        
        # Check database host
        db_host = settings.DATABASES['default'].get('HOST', 'localhost')
        if 'railway' in db_host.lower() or 'prod' in db_host.lower():
            return 'production'
        elif 'postgres_local' in db_host or db_host == 'db':
            return 'local'
        
        return 'unknown'

    def _show_environment_info(self, env_name):
        """Display current environment information"""
        self.stdout.write("\n📍 ENVIRONMENT")
        self.stdout.write("-" * 80)
        
        db_config = settings.DATABASES['default']
        self.stdout.write(f"  Environment:     {self.style.WARNING(env_name.upper())}")
        self.stdout.write(f"  Database Name:   {db_config['NAME']}")
        self.stdout.write(f"  Database Host:   {db_config.get('HOST', 'localhost')}")
        self.stdout.write(f"  Database Port:   {db_config.get('PORT', '5432')}")
        self.stdout.write(f"  Database Engine: {db_config['ENGINE']}")

    def _show_data_counts(self):
        """Display data counts for procurement models"""
        self.stdout.write("\n📊 DATA COUNTS")
        self.stdout.write("-" * 80)
        
        # Soft-coded model configuration
        models_to_check = {
            'Purchase Requisitions': PurchaseRequisition,
            'Purchase Orders': PurchaseOrder,
            'Vendors': Vendor,
            'Receipts': Receipt,
            'Users': User,
        }
        
        counts = {}
        for label, model in models_to_check.items():
            try:
                count = model.objects.count()
                counts[label] = count
                status_icon = "✓" if count > 0 else "⚠"
                self.stdout.write(f"  {status_icon} {label:25s}: {count:6d}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ {label:25s}: Error - {str(e)[:50]}"))
        
        return counts

    def _show_migration_status(self):
        """Display migration status for procurement app"""
        self.stdout.write("\n🔄 MIGRATION STATUS")
        self.stdout.write("-" * 80)
        
        # Soft-coded migrations to check
        critical_migrations = [
            '0013_enhance_pr_workflow_and_vendor_integration',
            '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more',
        ]
        
        with connection.cursor() as cursor:
            # Get all procurement migrations
            cursor.execute("""
                SELECT name, applied 
                FROM django_migrations 
                WHERE app = 'procurement'
                ORDER BY applied DESC 
                LIMIT 10
            """)
            migrations = cursor.fetchall()
            
            self.stdout.write(f"  Total migrations applied: {len(migrations)}")
            
            # Check critical migrations
            cursor.execute("""
                SELECT name 
                FROM django_migrations 
                WHERE app = 'procurement'
            """)
            applied_names = [row[0] for row in cursor.fetchall()]
            
            self.stdout.write("\n  Critical migrations:")
            for mig_name in critical_migrations:
                is_applied = any(mig_name in name for name in applied_names)
                if is_applied:
                    self.stdout.write(self.style.SUCCESS(f"    ✓ {mig_name}"))
                else:
                    self.stdout.write(self.style.ERROR(f"    ✗ {mig_name} - NOT APPLIED"))

    def _show_sample_data(self):
        """Display sample records (verbose mode)"""
        self.stdout.write("\n📋 SAMPLE RECORDS")
        self.stdout.write("-" * 80)
        
        # Show sample PRs
        pr_count = PurchaseRequisition.objects.count()
        if pr_count > 0:
            self.stdout.write("\n  Latest Purchase Requisitions:")
            for pr in PurchaseRequisition.objects.order_by('-created_at')[:3]:
                self.stdout.write(f"    • {pr.pr_number} | {pr.status} | {pr.created_at.strftime('%Y-%m-%d')}")
        
        # Show sample POs
        po_count = PurchaseOrder.objects.count()
        if po_count > 0:
            self.stdout.write("\n  Latest Purchase Orders:")
            for po in PurchaseOrder.objects.order_by('-created_at')[:3]:
                self.stdout.write(f"    • {po.po_number} | {po.status} | {po.created_at.strftime('%Y-%m-%d')}")

    def _show_health_check(self):
        """Display health check and recommendations"""
        self.stdout.write("\n🏥 HEALTH CHECK")
        self.stdout.write("-" * 80)
        
        issues = []
        warnings = []
        
        # Check data
        pr_count = PurchaseRequisition.objects.count()
        po_count = PurchaseOrder.objects.count()
        
        if pr_count == 0 and po_count == 0:
            issues.append("No procurement data found in database")
            warnings.append("Consider running: python manage.py seed_procurement_data")
        
        # Check migrations
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM django_migrations 
                WHERE app = 'procurement' 
                AND (name LIKE '%0013%' OR name LIKE '%0014%')
            """)
            new_mig_count = cursor.fetchone()[0]
            
            if new_mig_count < 2:
                issues.append("Recent migrations (0013, 0014) not fully applied")
                warnings.append("Run: python manage.py migrate procurement")
        
        # Display results
        if not issues:
            self.stdout.write(self.style.SUCCESS("  ✓ All checks passed"))
        else:
            self.stdout.write(self.style.ERROR("  Issues found:"))
            for issue in issues:
                self.stdout.write(f"    ✗ {issue}")
        
        if warnings:
            self.stdout.write(self.style.WARNING("\n  Recommendations:"))
            for warning in warnings:
                self.stdout.write(f"    → {warning}")
