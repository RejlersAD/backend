"""
Django Management Command: Fix Production Procurement
Complete automated fix: migrations + data sync in one command
"""

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
import sys

User = get_user_model()


class Command(BaseCommand):
    help = 'Fix production procurement: run migrations and optionally seed data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check-only',
            action='store_true',
            help='Only check status, do not make changes',
        )
        parser.add_argument(
            '--seed',
            action='store_true',
            help='Seed sample data after fixing migrations',
        )

    def handle(self, *args, **options):
        check_only = options['check_only']
        seed = options['seed']
        
        self.stdout.write("=" * 80)
        self.stdout.write("PRODUCTION PROCUREMENT FIX")
        self.stdout.write("=" * 80)
        
        # Step 1: Check migrations
        self.stdout.write("\n[1/4] Checking migrations...")
        migration_status = self._check_migrations()
        
        if not migration_status['0013'] or not migration_status['0014']:
            if check_only:
                self.stdout.write(self.style.ERROR("  Migrations NOT applied (check-only mode)"))
            else:
                self.stdout.write("  Migrations missing - attempting to apply...")
                self._apply_migrations()
        else:
            self.stdout.write(self.style.SUCCESS("  Migrations already applied"))
        
        # Step 2: Verify columns
        self.stdout.write("\n[2/4] Verifying database schema...")
        columns_ok = self._verify_columns()
        
        if not columns_ok:
            self.stdout.write(self.style.ERROR("  Schema issues detected"))
            if not check_only:
                self.stdout.write("  Attempting manual column creation...")
                self._fix_schema()
        else:
            self.stdout.write(self.style.SUCCESS("  Schema OK"))
        
        # Step 3: Check data
        self.stdout.write("\n[3/4] Checking data...")
        data_counts = self._check_data()
        self.stdout.write(f"  Purchase Requisitions: {data_counts['pr']}")
        self.stdout.write(f"  Purchase Orders: {data_counts['po']}")
        self.stdout.write(f"  Vendors: {data_counts['vendor']}")
        
        # Step 4: Seed if requested
        if seed and not check_only:
            self.stdout.write("\n[4/4] Seeding data...")
            self._seed_data()
        elif check_only:
            self.stdout.write("\n[4/4] Skipping data seeding (check-only mode)")
        else:
            self.stdout.write("\n[4/4] Skipping data seeding (use --seed to enable)")
        
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("COMPLETE"))
        self.stdout.write("=" * 80)

    def _check_migrations(self):
        """Check if critical migrations are applied"""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT name 
                FROM django_migrations 
                WHERE app = 'procurement'
                AND (name LIKE '%0013%' OR name LIKE '%0014%')
            """)
            applied = [row[0] for row in cursor.fetchall()]
            
            return {
                '0013': any('0013' in m for m in applied),
                '0014': any('0014' in m for m in applied),
            }

    def _apply_migrations(self):
        """Apply missing migrations"""
        from django.core.management import call_command
        try:
            call_command('migrate', 'procurement', verbosity=0)
            self.stdout.write(self.style.SUCCESS("  Migrations applied successfully"))
            return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Migration failed: {e}"))
            return False

    def _verify_columns(self):
        """Verify required columns exist"""
        required_columns = ['vendor_id', 'vendor_selection_reason', 'ai_vendor_recommendations']
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'procurement_purchaserequisition'
            """)
            existing_columns = [row[0] for row in cursor.fetchall()]
            
            missing = [col for col in required_columns if col not in existing_columns]
            
            if missing:
                self.stdout.write(self.style.WARNING(f"  Missing columns: {', '.join(missing)}"))
                return False
            
            return True

    def _fix_schema(self):
        """Manually add missing columns via SQL"""
        with connection.cursor() as cursor:
            try:
                with transaction.atomic():
                    # Add vendor_id
                    cursor.execute("""
                        DO $$ 
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name = 'procurement_purchaserequisition' 
                                AND column_name = 'vendor_id'
                            ) THEN
                                ALTER TABLE procurement_purchaserequisition 
                                ADD COLUMN vendor_id uuid NULL
                                REFERENCES procurement_vendor(id) ON DELETE SET NULL;
                                
                                CREATE INDEX IF NOT EXISTS procurement_purchas_vendor_id_idx 
                                ON procurement_purchaserequisition(vendor_id);
                            END IF;
                        END $$;
                    """)
                    
                    # Add vendor_selection_reason
                    cursor.execute("""
                        DO $$ 
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name = 'procurement_purchaserequisition' 
                                AND column_name = 'vendor_selection_reason'
                            ) THEN
                                ALTER TABLE procurement_purchaserequisition 
                                ADD COLUMN vendor_selection_reason text NOT NULL DEFAULT '';
                            END IF;
                        END $$;
                    """)
                    
                    # Add ai_vendor_recommendations
                    cursor.execute("""
                        DO $$ 
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name = 'procurement_purchaserequisition' 
                                AND column_name = 'ai_vendor_recommendations'
                            ) THEN
                                ALTER TABLE procurement_purchaserequisition 
                                ADD COLUMN ai_vendor_recommendations jsonb NOT NULL DEFAULT '[]'::jsonb;
                            END IF;
                        END $$;
                    """)
                    
                    # Record migrations
                    cursor.execute("""
                        INSERT INTO django_migrations (app, name, applied)
                        SELECT 'procurement', '0013_enhance_pr_workflow_and_vendor_integration', NOW()
                        WHERE NOT EXISTS (
                            SELECT 1 FROM django_migrations 
                            WHERE app = 'procurement' 
                            AND name = '0013_enhance_pr_workflow_and_vendor_integration'
                        )
                    """)
                    
                    cursor.execute("""
                        INSERT INTO django_migrations (app, name, applied)
                        SELECT 'procurement', '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more', NOW()
                        WHERE NOT EXISTS (
                            SELECT 1 FROM django_migrations 
                            WHERE app = 'procurement' 
                            AND name = '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more'
                        )
                    """)
                    
                self.stdout.write(self.style.SUCCESS("  Schema fixed manually"))
                return True
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Schema fix failed: {e}"))
                return False

    def _check_data(self):
        """Check current data counts"""
        return {
            'pr': PurchaseRequisition.objects.count(),
            'po': PurchaseOrder.objects.count(),
            'vendor': Vendor.objects.count(),
        }

    def _seed_data(self):
        """Seed sample data"""
        from django.core.management import call_command
        try:
            call_command('seed_procurement_data', '--vendors=10', '--prs=15', '--pos=10', verbosity=0)
            self.stdout.write(self.style.SUCCESS("  Sample data seeded"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Seeding failed: {e}"))
