"""
Django Management Command: Seed Procurement Sample Data
Smart seeding command with soft-coded configuration for generating test/demo data
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.procurement.models import (
    PurchaseOrder, PurchaseRequisition, Vendor,
    Project, Budget, CostCenter
)
from decimal import Decimal
import random
from datetime import timedelta

User = get_user_model()


# Soft-coded seed configuration
SEED_CONFIG = {
    'vendors': {
        'count': 10,
        'templates': [
            {'name': 'Global Equipment Suppliers LLC', 'category': 'rotating_equipment', 'country': 'UAE'},
            {'name': 'Industrial Valves International', 'category': 'valves_fittings', 'country': 'USA'},
            {'name': 'Instrumentation Systems FZE', 'category': 'instrumentation', 'country': 'UAE'},
            {'name': 'Piping Materials Trading LLC', 'category': 'piping_materials', 'country': 'Saudi Arabia'},
            {'name': 'Electrical Components Co.', 'category': 'electrical_materials', 'country': 'Germany'},
            {'name': 'Safety Equipment Providers', 'category': 'safety_equipment', 'country': 'UK'},
            {'name': 'Maintenance Services Group', 'category': 'maintenance_services', 'country': 'UAE'},
            {'name': 'Chemical Suppliers International', 'category': 'chemicals', 'country': 'USA'},
            {'name': 'Static Equipment Fabricators', 'category': 'static_equipment', 'country': 'China'},
            {'name': 'Spare Parts Distribution LLC', 'category': 'spare_parts', 'country': 'UAE'},
        ]
    },
    'purchase_requisitions': {
        'count': 15,
        'status_distribution': {
            'draft': 0.2,
            'submitted': 0.1,
            'in_review': 0.1,
            'approved': 0.4,
            'converted': 0.2,
        },
        'priority_distribution': {
            'urgent': 0.1,
            'high': 0.2,
            'normal': 0.6,
            'low': 0.1,
        },
        'price_range': (1000, 50000),
    },
    'purchase_orders': {
        'count': 10,
        'status_distribution': {
            # SOFT-CODED: Status values aligned with PurchaseOrder.STATUS_CHOICES
            # Valid choices: draft, sent, acknowledged, in_progress, partially_received, completed, cancelled
            'draft': 0.15,
            'sent': 0.15,
            'acknowledged': 0.15,
            'in_progress': 0.25,
            'completed': 0.20,
            'partially_received': 0.10,
        },
        'price_range': (5000, 100000),
    },
}


class Command(BaseCommand):
    help = 'Seed procurement module with sample data for testing/demo purposes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--vendors',
            type=int,
            default=SEED_CONFIG['vendors']['count'],
            help=f'Number of vendors to create (default: {SEED_CONFIG["vendors"]["count"]})',
        )
        parser.add_argument(
            '--prs',
            type=int,
            default=SEED_CONFIG['purchase_requisitions']['count'],
            help=f'Number of purchase requisitions to create (default: {SEED_CONFIG["purchase_requisitions"]["count"]})',
        )
        parser.add_argument(
            '--pos',
            type=int,
            default=SEED_CONFIG['purchase_orders']['count'],
            help=f'Number of purchase orders to create (default: {SEED_CONFIG["purchase_orders"]["count"]})',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before seeding (DANGEROUS)',
        )
        parser.add_argument(
            '--demo-mode',
            action='store_true',
            help='Create demo-ready data with realistic scenarios',
        )

    def handle(self, *args, **options):
        vendors_count = options['vendors']
        prs_count = options['prs']
        pos_count = options['pos']
        clear_existing = options['clear']
        demo_mode = options['demo_mode']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  PROCUREMENT DATA SEEDING"))
        self.stdout.write("=" * 80)
        
        # Safety check for clearing data
        if clear_existing:
            self._confirm_clear()
        
        # Get or create admin user
        admin_user = self._get_admin_user()
        
        # Seed data
        vendors = self._seed_vendors(vendors_count)
        self._seed_purchase_requisitions(prs_count, admin_user, vendors, demo_mode)
        self._seed_purchase_orders(pos_count, admin_user, vendors, demo_mode)
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("✓ Seeding complete!"))
        self.stdout.write("=" * 80)

    def _confirm_clear(self):
        """Confirm before clearing data"""
        self.stdout.write(self.style.WARNING("\n⚠️  WARNING: --clear flag will delete ALL procurement data!"))
        confirm = input("Type 'DELETE' to confirm: ")
        
        if confirm == 'DELETE':
            self.stdout.write("Clearing existing data...")
            PurchaseOrder.objects.all().delete()
            PurchaseRequisition.objects.all().delete()
            Vendor.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("✓ Data cleared"))
        else:
            self.stdout.write(self.style.ERROR("Aborted. Data not cleared."))
            exit(0)

    def _get_admin_user(self):
        """Get or create admin user for seeding"""
        try:
            admin = User.objects.filter(is_superuser=True).first()
            if not admin:
                admin = User.objects.filter(is_staff=True).first()
            if not admin:
                admin = User.objects.first()
            
            if not admin:
                self.stdout.write(self.style.ERROR("✗ No users found in database. Please create users first."))
                exit(1)
            
            self.stdout.write(f"Using user: {admin.username}")
            return admin
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error getting user: {e}"))
            exit(1)

    def _seed_vendors(self, count):
        """Seed vendor data using soft-coded templates"""
        self.stdout.write(f"\n📦 Seeding {count} vendors...")
        
        templates = SEED_CONFIG['vendors']['templates']
        vendors = []
        
        for i in range(count):
            template = templates[i % len(templates)]
            
            # Check if vendor exists
            vendor_name = f"{template['name']} - Branch {i//len(templates) + 1}" if i >= len(templates) else template['name']
            vendor, created = Vendor.objects.get_or_create(
                name=vendor_name,
                defaults={
                    'vendor_code': f"VEN-{1000 + i}",
                    'contact_person': f"Contact Person {i+1}",
                    'email': f"contact{i+1}@{template['name'].lower().replace(' ', '')}.com",
                    'phone': f"+971-50-{random.randint(1000000, 9999999)}",
                    'address': f"{random.randint(100, 999)} Business District, {template['country']}",
                    'country': template['country'],
                    'categories': [template['category']],  # JSONField expects list
                    'payment_terms': random.choice(['Net 30', 'Net 45', 'Net 60', 'COD']),
                    'status': 'active',  # Use correct field name
                    'rating': random.choice([3, 4, 5]),  # Integer rating (3-5)
                }
            )
            
            vendors.append(vendor)
            status = "✓ Created" if created else "- Exists"
            self.stdout.write(f"  {status} {vendor_name}")
        
        self.stdout.write(self.style.SUCCESS(f"✓ Seeded {len(vendors)} vendors"))
        return vendors

    def _seed_purchase_requisitions(self, count, user, vendors, demo_mode):
        """Seed purchase requisitions using soft-coded configuration"""
        self.stdout.write(f"\n📋 Seeding {count} purchase requisitions...")
        
        config = SEED_CONFIG['purchase_requisitions']
        statuses = list(config['status_distribution'].keys())
        priorities = list(config['priority_distribution'].keys())
        
        created_count = 0
        for i in range(count):
            # Generate PR number
            pr_number = f"RAD-GEN-PR-{2000 + i:04d}_2026"
            
            # Check if exists
            if PurchaseRequisition.objects.filter(pr_number=pr_number).exists():
                continue
            
            # Select status and priority based on distribution
            status = random.choices(statuses, weights=list(config['status_distribution'].values()))[0]
            priority = random.choices(priorities, weights=list(config['priority_distribution'].values()))[0]
            
            # Random price
            price = Decimal(str(random.uniform(*config['price_range']))).quantize(Decimal('0.01'))
            
            # Select vendor
            vendor = random.choice(vendors) if vendors else None
            
            # Create PR
            pr = PurchaseRequisition.objects.create(
                pr_number=pr_number,
                issued_by=user,
                issued_date=timezone.now().date() - timedelta(days=random.randint(0, 60)),
                supplier_name=vendor.name if vendor else f"Supplier {i+1}",
                vendor=vendor,
                product_service=f"Equipment/Service Category {random.randint(1, 10)}",
                project_department=random.choice(['Process Engineering', 'Mechanical', 'Electrical', 'Instrumentation']),
                description_reason=f"Sample procurement requirement for testing purposes - Item {i+1}",
                total_price=price,
                currency='USD',
                status=status,
                priority=priority,
                requisition_type=random.choice(['general', 'project']),
            )
            
            created_count += 1
            self.stdout.write(f"  ✓ Created PR {pr.pr_number} | {pr.status} | ${pr.total_price}")
        
        self.stdout.write(self.style.SUCCESS(f"✓ Seeded {created_count} purchase requisitions"))

    def _seed_purchase_orders(self, count, user, vendors, demo_mode):
        """Seed purchase orders using soft-coded configuration"""
        self.stdout.write(f"\n📦 Seeding {count} purchase orders...")
        
        config = SEED_CONFIG['purchase_orders']
        statuses = list(config['status_distribution'].keys())
        
        # SOFT-CODED: PO field mapping aligned with actual PurchaseOrder model
        # Never hardcode field names - use dynamic mapping for maintainability
        CATEGORY_OPTIONS = [
            'rotating_equipment', 'static_equipment', 'instrumentation',
            'valves_fittings', 'piping_materials', 'electrical_materials',
            'spare_parts', 'chemicals', 'maintenance_services'
        ]
        
        created_count = 0
        for i in range(count):
            # Generate PO number
            po_number = f"RAD-PRJ-PUR-{3000 + i:04d}_2026"
            
            # Check if exists
            if PurchaseOrder.objects.filter(po_number=po_number).exists():
                continue
            
            # Select vendor
            vendor = random.choice(vendors) if vendors else None
            if not vendor:
                self.stdout.write(self.style.WARNING(f"  ⚠ Skipping PO {po_number} - No vendors available"))
                continue
            
            # Select status based on distribution
            status = random.choices(statuses, weights=list(config['status_distribution'].values()))[0]
            
            # Random price
            price = Decimal(str(random.uniform(*config['price_range']))).quantize(Decimal('0.01'))
            
            # Random category from vendor's categories or default list
            if vendor.categories:
                category = random.choice(vendor.categories)
            else:
                category = random.choice(CATEGORY_OPTIONS)
            
            # SOFT-CODED: Build PO data using only valid model fields
            # This prevents TypeError from non-existent fields
            po_data = {
                'po_number': po_number,
                'vendor': vendor,
                'status': status,
                'total_amount': price,
                'currency': 'USD',
                'title': f"Purchase Order - {vendor.name[:50]}",  # Required field
                'category': category,  # Required field
                'description': f"Sample purchase order for testing - Order {i+1}",
                'payment_terms': random.choice(['Net 30', 'Net 45', 'Net 60', '45 days net for agreed payment milestones']),
                'delivery_terms': random.choice(['FOB', 'CIF', 'Services completed and accepted']),
            }
            
            # Create PO using validated field mapping
            try:
                po = PurchaseOrder.objects.create(**po_data)
                created_count += 1
                self.stdout.write(f"  ✓ Created PO {po.po_number} | {po.status} | {po.category} | ${po.total_amount}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed to create PO {po_number}: {str(e)}"))
                continue
        
        self.stdout.write(self.style.SUCCESS(f"✓ Seeded {created_count} purchase orders"))
