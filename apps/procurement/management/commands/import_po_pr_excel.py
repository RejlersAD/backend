"""
Django Management Command: Import PO and PR Data from Excel
Soft-coded migration script for transferring Excel records to PostgreSQL

USAGE:
    python manage.py import_po_pr_excel --file path/to/file.xlsx --type [po|pr] [--dry-run]

FEATURES:
- Soft-coded field mapping configuration
- Intelligent data cleaning and normalization
- Duplicate detection and handling
- Comprehensive validation and error reporting
- Dry-run mode for testing
- Progress tracking and statistics
"""

import os
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.procurement.models import (
    PurchaseOrder, PurchaseRequisition, Vendor, 
    Project, Budget, PROCUREMENT_CATEGORIES
)
import pandas as pd
import numpy as np

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# PO Excel Column Mapping (adjust based on actual Excel structure)
PO_COLUMN_MAPPING = {
    # Header Section
    'po_number': ['PO Number', 'PO No', 'Purchase Order Number', 'PO#'],
    'po_date': ['PO Date', 'Date', 'Order Date', 'Created Date'],
    'pr_reference': ['PR Number', 'PR No', 'Requisition Number', 'Reference PR'],
    
    # Vendor Section
    'vendor_name': ['Vendor Name', 'Supplier Name', 'Seller Name', 'Vendor'],
    'vendor_code': ['Vendor Code', 'Supplier Code', 'Vendor ID'],
    'seller_reference': ['Seller Reference', 'Contact Person', 'Attn'],
    'quote_ref': ['Quote Reference', 'Quote Ref', 'Quotation Reference'],
    'seller_license_no': ['License No', 'Business License', 'CN Number'],
    
    # Financial Section
    'total_amount': ['Total Amount', 'Total', 'Amount', 'Total Value', 'Grand Total'],
    'currency': ['Currency', 'Curr'],
    'tax_amount': ['Tax Amount', 'VAT Amount', 'Tax'],
    'vat_percentage': ['VAT %', 'Tax %', 'VAT Percentage'],
    
    # Project Section
    'project_number': ['Project Number', 'Project No', 'Project Code'],
    'project_name': ['Project Name', 'Project'],
    'end_client': ['End Client', 'Client', 'Customer'],
    'contractor': ['Contractor', 'Main Contractor'],
    
    # Approval Section
    'approved_by_name': ['Approved By', 'Approver Name', 'Approver'],
    'approved_by_title': ['Approver Title', 'Title', 'Designation'],
    'approved_date': ['Approval Date', 'Approved On'],
    
    # Details
    'title': ['Title', 'Description', 'Subject', 'PO Title'],
    'category': ['Category', 'Type', 'Classification'],
    'status': ['Status', 'PO Status', 'Order Status'],
    'payment_terms': ['Payment Terms', 'Terms', 'Payment Conditions'],
    'delivery_terms': ['Delivery Terms', 'Delivery', 'Incoterms'],
    'expected_delivery': ['Delivery Date', 'Expected Delivery', 'Required Date'],
    'notes': ['Notes', 'Remarks', 'Comments'],
}

# PR Excel Column Mapping
PR_COLUMN_MAPPING = {
    # Header Section
    'pr_number': ['PR Number', 'PR No', 'Requisition Number', 'PR#'],
    'issued_date': ['Date', 'Issued Date', 'Request Date', 'Created Date'],
    'issued_by': ['Issued By', 'Requested By', 'Requester'],
    
    # Supplier Section
    'supplier_name': ['Supplier Name', 'Preferred Supplier', 'Vendor Name'],
    'supplier_business_id': ['Business ID', 'Supplier ID', 'CN Number'],
    
    # Project/Product Section
    'product_service': ['Product/Service', 'Item Description', 'Description'],
    'project_department': ['Project/Department', 'Department', 'Project'],
    
    # Pricing Section
    'total_price': ['Total Price', 'Amount', 'Total', 'Price'],
    'currency': ['Currency', 'Curr'],
    'net_total_excl_vat': ['Net Total', 'Amount Excl VAT', 'Subtotal'],
    
    # Reference Section
    'po_number_reference': ['PO Reference', 'PO Number', 'Related PO'],
    
    # Details
    'description_reason': ['Description', 'Reason', 'Purpose', 'Justification'],
    'special_notes': ['Special Notes', 'Notes', 'Remarks'],
    'status': ['Status', 'PR Status', 'Approval Status'],
    'priority': ['Priority', 'Urgency'],
    'category': ['Category', 'Type', 'Classification'],
}

# Status normalization mapping
STATUS_NORMALIZATION = {
    'po': {
        'draft': ['draft', 'new', 'pending', 'created'],
        'sent': ['sent', 'submitted', 'issued', 'sent to vendor'],
        'acknowledged': ['acknowledged', 'confirmed', 'accepted'],
        'in_progress': ['in progress', 'processing', 'active', 'ongoing'],
        'partially_received': ['partially received', 'partial', 'partial delivery'],
        'completed': ['completed', 'closed', 'finished', 'delivered', 'received'],
        'cancelled': ['cancelled', 'canceled', 'void', 'rejected'],
    },
    'pr': {
        'draft': ['draft', 'new', 'pending'],
        'submitted': ['submitted', 'pending approval', 'submitted for approval'],
        'pm_approved': ['pm approved', 'manager approved', 'approved by pm'],
        'vp_approved': ['vp approved', 'senior approved'],
        'fully_approved': ['approved', 'fully approved', 'final approval'],
        'rejected': ['rejected', 'not approved', 'declined'],
        'cancelled': ['cancelled', 'canceled', 'void'],
        'converted': ['converted', 'po created', 'converted to po'],
    }
}

# Priority normalization
PRIORITY_NORMALIZATION = {
    'urgent': ['urgent', 'critical', 'emergency', 'high priority'],
    'high': ['high', 'important'],
    'normal': ['normal', 'medium', 'standard', 'regular'],
    'low': ['low', 'non-urgent'],
}

# Default values configuration
DEFAULTS = {
    'po': {
        'status': 'draft',
        'currency': 'USD',
        'vat_percentage': Decimal('5.00'),
        'category': 'other',
        'payment_mode': 'Bank Transfer',
    },
    'pr': {
        'status': 'draft',
        'currency': 'USD',
        'requisition_type': 'project',
        'priority': 'normal',
        'category': 'other',
        'form_reference': 'RAD-OM-PRC-0001 FRM -1 Rev 0',
        'page_number': 'Page 1 of 1',
    }
}


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

class DataImportStats:
    """Track import statistics"""
    def __init__(self):
        self.total_rows = 0
        self.successful = 0
        self.skipped = 0
        self.errors = 0
        self.duplicates = 0
        self.error_details = []
        self.created_items = []
        self.skipped_items = []
    
    def add_success(self, item_id, item_number):
        self.successful += 1
        self.created_items.append((item_id, item_number))
    
    def add_skip(self, reason, row_number=None):
        self.skipped += 1
        self.skipped_items.append((row_number, reason))
    
    def add_error(self, error_msg, row_number=None):
        self.errors += 1
        self.error_details.append((row_number, error_msg))
    
    def add_duplicate(self):
        self.duplicates += 1
        self.skipped += 1
    
    def print_summary(self, stdout):
        stdout.write("\n" + "="*80)
        stdout.write(self.style.SUCCESS("\n📊 IMPORT SUMMARY"))
        stdout.write("="*80 + "\n")
        stdout.write(f"Total Rows Processed: {self.total_rows}\n")
        stdout.write(self.style.SUCCESS(f"✅ Successfully Created: {self.successful}\n"))
        stdout.write(self.style.WARNING(f"⏭️  Skipped: {self.skipped} (including {self.duplicates} duplicates)\n"))
        stdout.write(self.style.ERROR(f"❌ Errors: {self.errors}\n"))
        
        if self.error_details:
            stdout.write("\n" + self.style.ERROR("Error Details:"))
            for row_num, error in self.error_details[:10]:  # Show first 10 errors
                stdout.write(f"  Row {row_num}: {error}\n")
            if len(self.error_details) > 10:
                stdout.write(f"  ... and {len(self.error_details) - 10} more errors\n")
        
        if self.skipped_items:
            stdout.write("\n" + self.style.WARNING("Skipped Items:"))
            for row_num, reason in self.skipped_items[:5]:
                stdout.write(f"  Row {row_num}: {reason}\n")
            if len(self.skipped_items) > 5:
                stdout.write(f"  ... and {len(self.skipped_items) - 5} more skipped items\n")
        
        stdout.write("="*80 + "\n")


def find_column(df, possible_names):
    """Find column by trying multiple possible names (case-insensitive)"""
    for col in df.columns:
        for possible_name in possible_names:
            if col.strip().lower() == possible_name.lower():
                return col
    return None


def clean_string(value):
    """Clean and normalize string values"""
    if pd.isna(value) or value is None:
        return ''
    
    value = str(value).strip()
    
    # Remove multiple spaces
    value = re.sub(r'\s+', ' ', value)
    
    return value


def parse_date(value):
    """Parse various date formats"""
    if pd.isna(value) or value is None or value == '':
        return None
    
    # If already a date object
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    
    # Try parsing string
    value = str(value).strip()
    
    # Common date formats
    date_formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%m/%d/%Y',
        '%d-%m-%Y',
        '%d.%m.%Y',
        '%Y/%m/%d',
        '%d %B %Y',
        '%d %b %Y',
        '%B %d, %Y',
    ]
    
    for fmt in date_formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    
    # Try pandas parsing as last resort
    try:
        return pd.to_datetime(value).date()
    except:
        return None


def parse_decimal(value):
    """Parse decimal values from various formats"""
    if pd.isna(value) or value is None or value == '':
        return None
    
    # Convert to string and clean
    value = str(value).strip()
    
    # Remove currency symbols and commas
    value = re.sub(r'[^\d.-]', '', value)
    
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def normalize_status(value, status_type='po'):
    """Normalize status values to match model choices"""
    if pd.isna(value) or not value:
        return None
    
    value_lower = str(value).strip().lower()
    
    mappings = STATUS_NORMALIZATION.get(status_type, {})
    
    for standard_status, variations in mappings.items():
        if value_lower in variations:
            return standard_status
    
    return None


def normalize_priority(value):
    """Normalize priority values"""
    if pd.isna(value) or not value:
        return 'normal'
    
    value_lower = str(value).strip().lower()
    
    for standard_priority, variations in PRIORITY_NORMALIZATION.items():
        if value_lower in variations:
            return standard_priority
    
    return 'normal'


def get_or_create_vendor(vendor_name, vendor_code=None, defaults=None):
    """Get or create vendor by name or code"""
    if not vendor_name or vendor_name.strip() == '':
        return None
    
    vendor_name = clean_string(vendor_name)
    
    # Try to find by code first
    if vendor_code and vendor_code.strip():
        vendor_code = clean_string(vendor_code)
        vendor = Vendor.objects.filter(vendor_code__iexact=vendor_code).first()
        if vendor:
            return vendor
    
    # Try to find by name
    vendor = Vendor.objects.filter(name__iexact=vendor_name).first()
    if vendor:
        return vendor
    
    # Create new vendor
    if not vendor_code:
        # Generate vendor code from name
        vendor_code = 'V-' + ''.join([c for c in vendor_name if c.isalnum()])[:20].upper()
        # Ensure uniqueness
        base_code = vendor_code
        counter = 1
        while Vendor.objects.filter(vendor_code=vendor_code).exists():
            vendor_code = f"{base_code}-{counter}"
            counter += 1
    
    vendor_defaults = defaults or {}
    vendor_defaults.setdefault('status', 'active')
    vendor_defaults.setdefault('contact_person', 'N/A')
    vendor_defaults.setdefault('email', f"{vendor_code.lower()}@vendor.com")
    vendor_defaults.setdefault('phone', 'N/A')
    
    vendor = Vendor.objects.create(
        vendor_code=vendor_code,
        name=vendor_name,
        **vendor_defaults
    )
    
    return vendor


def get_default_user():
    """Get default user for created_by/issued_by fields"""
    # Try to get admin user or first user
    user = User.objects.filter(is_staff=True).first()
    if not user:
        user = User.objects.first()
    return user


# ═══════════════════════════════════════════════════════════════════════════
# MAIN IMPORT LOGIC
# ═══════════════════════════════════════════════════════════════════════════

class Command(BaseCommand):
    help = 'Import Purchase Orders and Purchase Requisitions from Excel files'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            required=True,
            help='Path to Excel file'
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['po', 'pr', 'auto'],
            default='auto',
            help='Type of data to import (po=Purchase Order, pr=Purchase Requisition, auto=detect)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run in dry-run mode (no database changes)'
        )
        parser.add_argument(
            '--sheet',
            type=str,
            default=None,
            help='Sheet name to import (default: first sheet)'
        )
    
    def handle(self, *args, **options):
        file_path = options['file']
        import_type = options['type']
        dry_run = options['dry_run']
        sheet_name = options['sheet'] or 0  # 0 = first sheet
        
        # Validate file exists
        if not os.path.exists(file_path):
            raise CommandError(f"File not found: {file_path}")
        
        self.stdout.write(self.style.SUCCESS(f"\n🚀 Starting Excel Import"))
        self.stdout.write(f"File: {file_path}")
        self.stdout.write(f"Type: {import_type}")
        self.stdout.write(f"Mode: {'DRY RUN' if dry_run else 'LIVE IMPORT'}")
        self.stdout.write("=" * 80 + "\n")
        
        # Read Excel file
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            self.stdout.write(f"📄 Loaded {len(df)} rows from Excel\n")
        except Exception as e:
            raise CommandError(f"Failed to read Excel file: {str(e)}")
        
        # Auto-detect type if needed
        if import_type == 'auto':
            import_type = self.detect_import_type(df)
            self.stdout.write(self.style.WARNING(f"🔍 Auto-detected type: {import_type.upper()}\n"))
        
        # Import based on type
        if import_type == 'po':
            stats = self.import_purchase_orders(df, dry_run)
        elif import_type == 'pr':
            stats = self.import_purchase_requisitions(df, dry_run)
        else:
            raise CommandError(f"Invalid import type: {import_type}")
        
        # Print summary
        stats.print_summary(self.stdout)
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  DRY RUN MODE - No changes were made to the database"))
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ Import completed successfully!"))
    
    def detect_import_type(self, df):
        """Auto-detect whether Excel contains PO or PR data"""
        columns_lower = [col.lower() for col in df.columns]
        
        po_indicators = ['purchase order', 'po number', 'po date', 'po_number']
        pr_indicators = ['purchase requisition', 'pr number', 'requisition', 'pr_number']
        
        po_score = sum(1 for indicator in po_indicators if any(indicator in col for col in columns_lower))
        pr_score = sum(1 for indicator in pr_indicators if any(indicator in col for col in columns_lower))
        
        return 'po' if po_score >= pr_score else 'pr'
    
    def import_purchase_orders(self, df, dry_run=False):
        """Import Purchase Order records from DataFrame"""
        stats = DataImportStats()
        stats.total_rows = len(df)
        
        self.stdout.write(self.style.SUCCESS("\n📦 Importing Purchase Orders...\n"))
        
        default_user = get_default_user()
        
        for index, row in df.iterrows():
            row_num = index + 2  # Excel row number (header is row 1)
            
            try:
                # Extract PO number (required field)
                po_number_col = find_column(df, PO_COLUMN_MAPPING['po_number'])
                po_number = clean_string(row[po_number_col] if po_number_col else row.iloc[0])
                
                if not po_number:
                    stats.add_skip("Missing PO number", row_num)
                    continue
                
                # Check for duplicates
                if PurchaseOrder.objects.filter(po_number=po_number).exists():
                    stats.add_duplicate()
                    self.stdout.write(f"  Row {row_num}: Skipped duplicate PO {po_number}")
                    continue
                
                # Extract vendor information
                vendor_name_col = find_column(df, PO_COLUMN_MAPPING['vendor_name'])
                vendor_code_col = find_column(df, PO_COLUMN_MAPPING['vendor_code'])
                
                vendor_name = clean_string(row[vendor_name_col]) if vendor_name_col else ''
                vendor_code = clean_string(row[vendor_code_col]) if vendor_code_col else None
                
                if not vendor_name:
                    stats.add_error(f"Missing vendor name for PO {po_number}", row_num)
                    continue
                
                vendor = get_or_create_vendor(vendor_name, vendor_code)
                
                # Extract all other fields using mapping
                data = self.extract_po_data(df, row, po_number)
                
                # Create PO
                if not dry_run:
                    with transaction.atomic():
                        po = PurchaseOrder.objects.create(
                            po_number=po_number,
                            vendor=vendor,
                            created_by=default_user,
                            **data
                        )
                        stats.add_success(str(po.id), po_number)
                        self.stdout.write(self.style.SUCCESS(f"  ✓ Row {row_num}: Created PO {po_number}"))
                else:
                    stats.add_success('dry-run', po_number)
                    self.stdout.write(f"  [DRY RUN] Would create PO {po_number}")
            
            except Exception as e:
                stats.add_error(f"{str(e)}", row_num)
                self.stdout.write(self.style.ERROR(f"  ✗ Row {row_num}: Error - {str(e)}"))
        
        return stats
    
    def extract_po_data(self, df, row, po_number):
        """Extract PO data from Excel row"""
        data = {}
        
        # Simple field extractions
        simple_fields = [
            ('title', 'title', clean_string),
            ('description', 'description', clean_string),
            ('category', 'category', clean_string),
            ('payment_terms', 'payment_terms', clean_string),
            ('delivery_terms', 'delivery_terms', clean_string),
            ('notes', 'notes', clean_string),
            ('seller_reference', 'seller_reference', clean_string),
            ('quote_ref', 'quote_ref', clean_string),
            ('seller_license_no', 'seller_license_no', clean_string),
            ('project_number', 'project_number', clean_string),
            ('project_name', 'project', clean_string),
            ('end_client', 'end_client', clean_string),
            ('contractor', 'contractor', clean_string),
            ('approved_by_name', 'approved_by_name', clean_string),
            ('approved_by_title', 'approved_by_title', clean_string),
        ]
        
        for field_name, mapping_key, clean_func in simple_fields:
            col = find_column(df, PO_COLUMN_MAPPING.get(mapping_key, [field_name]))
            if col and col in row.index:
                value = clean_func(row[col])
                if value:
                    data[field_name] = value
        
        # Decimal fields
        decimal_fields = [
            ('total_amount', 'total_amount'),
            ('tax_amount', 'tax_amount'),
            ('vat_percentage', 'vat_percentage'),
        ]
        
        for field_name, mapping_key in decimal_fields:
            col = find_column(df, PO_COLUMN_MAPPING.get(mapping_key, [field_name]))
            if col and col in row.index:
                value = parse_decimal(row[col])
                if value is not None:
                    data[field_name] = value
        
        # Date fields
        date_fields = [
            ('po_date', 'po_date'),
            ('approved_date', 'approved_date'),
            ('expected_delivery', 'expected_delivery'),
        ]
        
        for field_name, mapping_key in date_fields:
            col = find_column(df, PO_COLUMN_MAPPING.get(mapping_key, [field_name]))
            if col and col in row.index:
                value = parse_date(row[col])
                if value:
                    data[field_name] = value
        
        # Status normalization
        status_col = find_column(df, PO_COLUMN_MAPPING.get('status', ['status']))
        if status_col and status_col in row.index:
            status = normalize_status(row[status_col], 'po')
            if status:
                data['status'] = status
        
        # Currency
        currency_col = find_column(df, PO_COLUMN_MAPPING.get('currency', ['currency']))
        if currency_col and currency_col in row.index:
            currency = clean_string(row[currency_col]).upper()
            if currency in ['USD', 'AED', 'EUR', 'GBP']:
                data['currency'] = currency
        
        # Apply defaults
        for key, value in DEFAULTS['po'].items():
            data.setdefault(key, value)
        
        # Ensure title exists
        if 'title' not in data or not data['title']:
            data['title'] = f"Purchase Order {po_number}"
        
        # Ensure total_amount exists
        if 'total_amount' not in data:
            data['total_amount'] = Decimal('0.00')
        
        return data
    
    def import_purchase_requisitions(self, df, dry_run=False):
        """Import Purchase Requisition records from DataFrame"""
        stats = DataImportStats()
        stats.total_rows = len(df)
        
        self.stdout.write(self.style.SUCCESS("\n📝 Importing Purchase Requisitions...\n"))
        
        default_user = get_default_user()
        
        for index, row in df.iterrows():
            row_num = index + 2
            
            try:
                # Extract PR number
                pr_number_col = find_column(df, PR_COLUMN_MAPPING['pr_number'])
                pr_number = clean_string(row[pr_number_col] if pr_number_col else row.iloc[0])
                
                if not pr_number:
                    stats.add_skip("Missing PR number", row_num)
                    continue
                
                # Check for duplicates
                if PurchaseRequisition.objects.filter(pr_number=pr_number).exists():
                    stats.add_duplicate()
                    self.stdout.write(f"  Row {row_num}: Skipped duplicate PR {pr_number}")
                    continue
                
                # Extract all fields
                data = self.extract_pr_data(df, row, pr_number)
                
                # Create PR
                if not dry_run:
                    with transaction.atomic():
                        pr = PurchaseRequisition.objects.create(
                            pr_number=pr_number,
                            issued_by=default_user,
                            **data
                        )
                        stats.add_success(str(pr.id), pr_number)
                        self.stdout.write(self.style.SUCCESS(f"  ✓ Row {row_num}: Created PR {pr_number}"))
                else:
                    stats.add_success('dry-run', pr_number)
                    self.stdout.write(f"  [DRY RUN] Would create PR {pr_number}")
            
            except Exception as e:
                stats.add_error(f"{str(e)}", row_num)
                self.stdout.write(self.style.ERROR(f"  ✗ Row {row_num}: Error - {str(e)}"))
        
        return stats
    
    def extract_pr_data(self, df, row, pr_number):
        """Extract PR data from Excel row"""
        data = {}
        
        # Simple field extractions
        simple_fields = [
            ('supplier_name', 'supplier_name', clean_string),
            ('supplier_business_id', 'supplier_business_id', clean_string),
            ('product_service', 'product_service', clean_string),
            ('project_department', 'project_department', clean_string),
            ('description_reason', 'description_reason', clean_string),
            ('special_notes', 'special_notes', clean_string),
            ('po_number_reference', 'po_number_reference', clean_string),
            ('category', 'category', clean_string),
        ]
        
        for field_name, mapping_key, clean_func in simple_fields:
            col = find_column(df, PR_COLUMN_MAPPING.get(mapping_key, [field_name]))
            if col and col in row.index:
                value = clean_func(row[col])
                if value:
                    data[field_name] = value
        
        # Decimal fields
        decimal_fields = [
            ('total_price', 'total_price'),
            ('net_total_excl_vat', 'net_total_excl_vat'),
        ]
        
        for field_name, mapping_key in decimal_fields:
            col = find_column(df, PR_COLUMN_MAPPING.get(mapping_key, [field_name]))
            if col and col in row.index:
                value = parse_decimal(row[col])
                if value is not None:
                    data[field_name] = value
        
        # Date fields
        date_col = find_column(df, PR_COLUMN_MAPPING.get('issued_date', ['issued_date']))
        if date_col and date_col in row.index:
            value = parse_date(row[date_col])
            if value:
                data['issued_date'] = value
        
        # Status normalization
        status_col = find_column(df, PR_COLUMN_MAPPING.get('status', ['status']))
        if status_col and status_col in row.index:
            status = normalize_status(row[status_col], 'pr')
            if status:
                data['status'] = status
        
        # Priority
        priority_col = find_column(df, PR_COLUMN_MAPPING.get('priority', ['priority']))
        if priority_col and priority_col in row.index:
            priority = normalize_priority(row[priority_col])
            data['priority'] = priority
        
        # Currency
        currency_col = find_column(df, PR_COLUMN_MAPPING.get('currency', ['currency']))
        if currency_col and currency_col in row.index:
            currency = clean_string(row[currency_col]).upper()
            if currency in ['USD', 'AED', 'EUR', 'GBP']:
                data['currency'] = currency
        
        # Apply defaults
        for key, value in DEFAULTS['pr'].items():
            data.setdefault(key, value)
        
        # Generate title from product_service if not provided
        if 'title' not in data or not data['title']:
            if 'product_service' in data:
                data['title'] = data['product_service'][:300]
            else:
                data['title'] = f"Purchase Requisition {pr_number}"
        
        return data
