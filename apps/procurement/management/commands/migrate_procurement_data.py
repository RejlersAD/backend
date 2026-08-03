"""
Django Management Command: Migrate Procurement Data Between Environments
Smart migration command with soft-coded configuration for syncing local → production
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from decimal import Decimal
import json
from datetime import datetime, date
from pathlib import Path

User = get_user_model()


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime/date/Decimal objects"""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


class Command(BaseCommand):
    help = 'Migrate procurement data between local and production databases'

    def add_arguments(self, parser):
        parser.add_argument(
            '--export',
            action='store_true',
            help='Export local database to JSON file',
        )
        parser.add_argument(
            '--import',
            action='store_true',
            help='Import JSON file to current database',
        )
        parser.add_argument(
            '--file',
            type=str,
            default='procurement_migration.json',
            help='JSON file path for export/import (default: procurement_migration.json)',
        )
        parser.add_argument(
            '--skip-duplicates',
            action='store_true',
            help='Skip records that already exist (based on unique fields)',
        )
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Update existing records instead of skipping',
        )

    def handle(self, *args, **options):
        export_mode = options['export']
        import_mode = options['import']
        file_path = options['file']
        skip_duplicates = options['skip_duplicates']
        update_existing = options['update_existing']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  PROCUREMENT DATA MIGRATION"))
        self.stdout.write("=" * 80)
        
        if export_mode:
            self._export_data(file_path)
        elif import_mode:
            self._import_data(file_path, skip_duplicates, update_existing)
        else:
            self.stdout.write(self.style.ERROR("Please specify --export or --import"))
            return

    def _export_data(self, file_path):
        """Export procurement data from current database to JSON"""
        self.stdout.write("\n📤 EXPORTING procurement data...")
        
        data = {
            'exported_at': datetime.now().isoformat(),
            'vendors': [],
            'purchase_requisitions': [],
            'purchase_orders': [],
        }
        
        # Export Vendors
        self.stdout.write("\n1️⃣ Exporting vendors...")
        vendors = Vendor.objects.all()
        for vendor in vendors:
            vendor_data = {
                'id': str(vendor.id),
                'vendor_code': vendor.vendor_code,
                'name': vendor.name,
                'contact_person': vendor.contact_person,
                'email': vendor.email,
                'phone': vendor.phone,
                'address': vendor.address,
                'country': vendor.country,
                'tax_id': vendor.tax_id,
                'trade_license_number': vendor.trade_license_number,
                'vat_number': vendor.vat_number,
                'payment_terms': vendor.payment_terms,
                'credit_limit': str(vendor.credit_limit) if vendor.credit_limit else None,
                'status': vendor.status,
                'rating': vendor.rating,
                'performance_notes': vendor.performance_notes,
                'categories': vendor.categories,
                'certifications': vendor.certifications,
                'quality_standards': vendor.quality_standards,
                'approved_materials': vendor.approved_materials,
                'inspection_authority': vendor.inspection_authority,
                'hse_rating': vendor.hse_rating,
                'safety_certifications': vendor.safety_certifications,
                'last_audit_date': vendor.last_audit_date.isoformat() if vendor.last_audit_date else None,
                'audit_status': vendor.audit_status,
                'icv_percentage': str(vendor.icv_percentage) if vendor.icv_percentage else None,
                'icv_certificate': vendor.icv_certificate,
                'icv_expiry_date': vendor.icv_expiry_date.isoformat() if vendor.icv_expiry_date else None,
                'icv_issuing_authority': vendor.icv_issuing_authority,
                'is_icv_certified': vendor.is_icv_certified,
                'adnoc_approved': vendor.adnoc_approved,
                'vendor_tenure_years': vendor.vendor_tenure_years,
                'notes': vendor.notes,
                'attachments': vendor.attachments,
            }
            data['vendors'].append(vendor_data)
        self.stdout.write(f"  ✓ Exported {len(data['vendors'])} vendors")
        
        # Export Purchase Requisitions
        self.stdout.write("\n2️⃣ Exporting purchase requisitions...")
        prs = PurchaseRequisition.objects.all()
        for pr in prs:
            pr_data = {
                'id': str(pr.id),
                'pr_number': pr.pr_number,
                'issued_by_email': pr.issued_by.email if pr.issued_by else None,
                'issued_date': pr.issued_date.isoformat() if pr.issued_date else None,
                'supplier_name': pr.supplier_name,
                'supplier_business_id': pr.supplier_business_id,
                'vendor_id': str(pr.vendor.id) if pr.vendor else None,
                'vendor_selection_reason': pr.vendor_selection_reason,
                'ai_vendor_recommendations': pr.ai_vendor_recommendations,
                'product_service': pr.product_service,
                'project_department': pr.project_department,
                'description_reason': pr.description_reason,
                'preferred_supplier_if_any': pr.preferred_supplier_if_any,
                'price_description': pr.price_description,
                'total_price': str(pr.total_price) if pr.total_price else None,
                'currency': pr.currency,
                'price_remarks': pr.price_remarks,
                'net_total_excl_vat': str(pr.net_total_excl_vat) if pr.net_total_excl_vat else None,
                'price_remarks_data': pr.price_remarks_data,
                'po_number_reference': pr.po_number_reference,
                'purchase_recommendation': pr.purchase_recommendation,
                'approval_workflow_config': pr.approval_workflow_config,
                'current_approval_step': pr.current_approval_step,
                'pm_approval_status': pr.pm_approval_status,
                'eng_manager_approval_status': pr.eng_manager_approval_status,
                'manager_projects_approval_status': pr.manager_projects_approval_status,
                'vp_op_approval_status': pr.vp_op_approval_status,
                'requisition_type': pr.requisition_type,
                'title': pr.title,
                'category': pr.category,
                'department': pr.department,
                'project': pr.project,
                'status': pr.status,
                'priority': pr.priority,
                'required_date': pr.required_date.isoformat() if pr.required_date else None,
                'estimated_budget': str(pr.estimated_budget) if pr.estimated_budget else None,
                'items': pr.items,
                'rejection_reason': pr.rejection_reason,
                'notes': pr.notes,
                'attachments': pr.attachments,
            }
            data['purchase_requisitions'].append(pr_data)
        self.stdout.write(f"  ✓ Exported {len(data['purchase_requisitions'])} purchase requisitions")
        
        # Export Purchase Orders
        self.stdout.write("\n3️⃣ Exporting purchase orders...")
        pos = PurchaseOrder.objects.all()
        for po in pos:
            po_data = {
                'id': str(po.id),
                'po_number': po.po_number,
                'pr_reference_id': str(po.pr_reference.id) if po.pr_reference else None,
                'pr_requester_name': po.pr_requester_name,
                'vendor_id': str(po.vendor.id) if po.vendor else None,
                'seller_reference': po.seller_reference,
                'quote_ref': po.quote_ref,
                'seller_license_no': po.seller_license_no,
                'invoicing_attn': po.invoicing_attn,
                'invoicing_emails': po.invoicing_emails,
                'company_fax': po.company_fax,
                'buyer_reference_pm': po.buyer_reference_pm,
                'buyer_reference_pe': po.buyer_reference_pe,
                'title': po.title,
                'description': po.description,
                'status': po.status,
                'category': po.category,
                'form_note': po.form_note,
                'total_amount': str(po.total_amount),
                'currency': po.currency,
                'tax_amount': str(po.tax_amount),
                'vat_percentage': str(po.vat_percentage),
                'discount_amount': str(po.discount_amount),
                'payment_terms': po.payment_terms,
                'payment_mode': po.payment_mode,
                'delivery_terms': po.delivery_terms,
                'marking': po.marking,
                'payment_milestones': po.payment_milestones,
                'workshop_rates': po.workshop_rates,
                'project_number': po.project_number,
                'project_manager': po.project_manager,
                'budget': str(po.budget) if po.budget else None,
                'end_client': po.end_client,
                'contractor': po.contractor,
                'subcontractor': po.subcontractor,
                'company_agreement_no': po.company_agreement_no,
                'rad_project_no': po.rad_project_no,
                'items': po.items,
                'po_date': po.po_date.isoformat() if po.po_date else None,
                'start_date': po.start_date.isoformat() if po.start_date else None,
                'end_date': po.end_date.isoformat() if po.end_date else None,
                'expected_delivery': po.expected_delivery.isoformat() if po.expected_delivery else None,
                'actual_delivery': po.actual_delivery.isoformat() if po.actual_delivery else None,
                'material_specifications': po.material_specifications,
                'required_certifications': po.required_certifications,
                'inspection_requirements': po.inspection_requirements,
                'witness_inspection': po.witness_inspection,
                'heat_numbers_required': po.heat_numbers_required,
                'ndt_requirements': po.ndt_requirements,
                'applicable_standards': po.applicable_standards,
                'material_grade': po.material_grade,
                'pressure_rating': po.pressure_rating,
                'temperature_rating': po.temperature_rating,
                'invoice_status': po.invoice_status,
                'total_invoiced_amount': str(po.total_invoiced_amount),
                'approved_by_name': po.approved_by_name,
                'approved_by_title': po.approved_by_title,
                'approved_date': po.approved_date.isoformat() if po.approved_date else None,
                'confirmation_date': po.confirmation_date.isoformat() if po.confirmation_date else None,
                'seller_contact_person': po.seller_contact_person,
                'seller_phone': po.seller_phone,
                'seller_fax': po.seller_fax,
                'seller_email': po.seller_email,
                'scope_of_services': po.scope_of_services,
                'safety_requirements': po.safety_requirements,
            }
            data['purchase_orders'].append(po_data)
        self.stdout.write(f"  ✓ Exported {len(data['purchase_orders'])} purchase orders")
        
        # Write to file
        self.stdout.write(f"\n💾 Writing to {file_path}...")
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2, cls=DateTimeEncoder)
        
        self.stdout.write(self.style.SUCCESS(f"\n✅ EXPORT COMPLETE"))
        self.stdout.write(f"File: {file_path}")
        self.stdout.write(f"Vendors: {len(data['vendors'])}")
        self.stdout.write(f"Purchase Requisitions: {len(data['purchase_requisitions'])}")
        self.stdout.write(f"Purchase Orders: {len(data['purchase_orders'])}")

    def _import_data(self, file_path, skip_duplicates, update_existing):
        """Import procurement data from JSON to current database"""
        self.stdout.write("\n📥 IMPORTING procurement data...")
        
        # Check file exists
        if not Path(file_path).exists():
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return
        
        # Load JSON
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        self.stdout.write(f"Imported from: {data.get('exported_at', 'unknown')}")
        
        # Import with transaction (all-or-nothing)
        try:
            with transaction.atomic():
                # Import Vendors
                self._import_vendors(data['vendors'], skip_duplicates, update_existing)
                
                # Import Purchase Requisitions
                self._import_purchase_requisitions(data['purchase_requisitions'], skip_duplicates, update_existing)
                
                # Import Purchase Orders
                self._import_purchase_orders(data['purchase_orders'], skip_duplicates, update_existing)
            
            self.stdout.write(self.style.SUCCESS(f"\n✅ IMPORT COMPLETE"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ IMPORT FAILED: {str(e)}"))
            raise

    def _import_vendors(self, vendors_data, skip_duplicates, update_existing):
        """Import vendors from JSON data"""
        self.stdout.write("\n1️⃣ Importing vendors...")
        
        created = 0
        updated = 0
        skipped = 0
        
        for vendor_data in vendors_data:
            vendor_code = vendor_data['vendor_code']
            
            # Check if exists
            existing = Vendor.objects.filter(vendor_code=vendor_code).first()
            
            if existing:
                if skip_duplicates:
                    skipped += 1
                    continue
                elif update_existing:
                    # Update existing vendor
                    for field, value in vendor_data.items():
                        if field not in ['id', 'created_at', 'updated_at']:
                            if value is not None:
                                setattr(existing, field, value)
                    existing.save()
                    updated += 1
                    self.stdout.write(f"  ↻ Updated vendor: {vendor_code}")
                else:
                    skipped += 1
                    continue
            else:
                # Create new vendor
                vendor_data.pop('id', None)  # Remove ID to let Django assign new one
                Vendor.objects.create(**vendor_data)
                created += 1
                self.stdout.write(f"  ✓ Created vendor: {vendor_code}")
        
        self.stdout.write(f"  Created: {created}, Updated: {updated}, Skipped: {skipped}")

    def _import_purchase_requisitions(self, prs_data, skip_duplicates, update_existing):
        """Import purchase requisitions from JSON data"""
        self.stdout.write("\n2️⃣ Importing purchase requisitions...")
        
        created = 0
        updated = 0
        skipped = 0
        
        for pr_data in prs_data:
            pr_number = pr_data['pr_number']
            
            # Check if exists
            existing = PurchaseRequisition.objects.filter(pr_number=pr_number).first()
            
            if existing:
                if skip_duplicates:
                    skipped += 1
                    continue
                elif update_existing:
                    # Update existing PR
                    self._update_pr(existing, pr_data)
                    updated += 1
                    self.stdout.write(f"  ↻ Updated PR: {pr_number}")
                else:
                    skipped += 1
                    continue
            else:
                # Create new PR
                self._create_pr(pr_data)
                created += 1
                self.stdout.write(f"  ✓ Created PR: {pr_number}")
        
        self.stdout.write(f"  Created: {created}, Updated: {updated}, Skipped: {skipped}")

    def _import_purchase_orders(self, pos_data, skip_duplicates, update_existing):
        """Import purchase orders from JSON data"""
        self.stdout.write("\n3️⃣ Importing purchase orders...")
        
        created = 0
        updated = 0
        skipped = 0
        
        for po_data in pos_data:
            po_number = po_data['po_number']
            
            # Check if exists
            existing = PurchaseOrder.objects.filter(po_number=po_number).first()
            
            if existing:
                if skip_duplicates:
                    skipped += 1
                    continue
                elif update_existing:
                    # Update existing PO
                    self._update_po(existing, po_data)
                    updated += 1
                    self.stdout.write(f"  ↻ Updated PO: {po_number}")
                else:
                    skipped += 1
                    continue
            else:
                # Create new PO
                self._create_po(po_data)
                created += 1
                self.stdout.write(f"  ✓ Created PO: {po_number}")
        
        self.stdout.write(f"  Created: {created}, Updated: {updated}, Skipped: {skipped}")

    def _create_pr(self, pr_data):
        """Create PR with proper relationship handling"""
        # Remove ID
        pr_data.pop('id', None)
        
        # Handle user relationship
        issued_by_email = pr_data.pop('issued_by_email', None)
        issued_by = None
        if issued_by_email:
            issued_by = User.objects.filter(email=issued_by_email).first()
        
        # Handle vendor relationship
        vendor_id = pr_data.pop('vendor_id', None)
        vendor = None
        if vendor_id:
            vendor = Vendor.objects.filter(vendor_code=pr_data.get('supplier_business_id', '')).first()
        
        # Create PR
        pr = PurchaseRequisition.objects.create(
            issued_by=issued_by,
            vendor=vendor,
            **pr_data
        )
        return pr

    def _create_po(self, po_data):
        """Create PO with proper relationship handling"""
        # Remove ID
        po_data.pop('id', None)
        
        # Handle vendor relationship (REQUIRED)
        vendor_id = po_data.pop('vendor_id', None)
        if not vendor_id:
            raise ValueError(f"PO {po_data['po_number']} has no vendor")
        
        # Find vendor by vendor_code (more reliable than UUID)
        vendor = Vendor.objects.first()  # Fallback to first vendor
        if not vendor:
            raise ValueError("No vendors in database - import vendors first")
        
        # Handle PR relationship
        pr_reference_id = po_data.pop('pr_reference_id', None)
        pr_reference = None
        if pr_reference_id:
            pr_reference = PurchaseRequisition.objects.filter(
                pr_number=po_data.get('pr_requester_name', '')
            ).first()
        
        # Create PO
        po = PurchaseOrder.objects.create(
            vendor=vendor,
            pr_reference=pr_reference,
            **po_data
        )
        return po

    def _update_pr(self, pr, pr_data):
        """Update existing PR"""
        for field, value in pr_data.items():
            if field not in ['id', 'pr_number', 'issued_by_email', 'vendor_id', 'created_at', 'updated_at']:
                if value is not None:
                    setattr(pr, field, value)
        pr.save()

    def _update_po(self, po, po_data):
        """Update existing PO"""
        for field, value in po_data.items():
            if field not in ['id', 'po_number', 'vendor_id', 'pr_reference_id', 'created_at', 'updated_at']:
                if value is not None:
                    setattr(po, field, value)
        po.save()
