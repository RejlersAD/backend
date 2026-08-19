"""
Management command to import vendors from Excel template
Usage: python manage.py import_vendors <excel_file_path>
"""

import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.procurement.models import Vendor

User = get_user_model()

# Soft-coded Excel column mapping
EXCEL_COLUMN_MAP = {
    'vendor_name': 'Vendor Name',
    'category': 'Category',
    'contact_person': 'Contact Person',
    'phone': 'Contact Number',
    'email': 'Email',
    'address': 'Location',
    'trade_license_number': 'Trade License#',
    'vat_number': 'VAT #',
    'is_icv_certified': 'ICV (Y/ N)',
    'adnoc_approved': 'ADNOC Approval',
    'vendor_tenure_years': 'Vendor Tenure (yrs)',
    'notes': 'Remarks ',
}

# Soft-coded default values
DEFAULTS = {
    'status': 'active',  # All vendors from approved list are active
    'country': 'United Arab Emirates',  # All vendors are UAE-based
    'rating': 4,  # Default good rating for approved vendors
}

# Soft-coded header skip rows
HEADER_SKIP_ROWS = 5  # Skip template header


class Command(BaseCommand):
    help = 'Import vendors from Excel template (RAD-PU-LST-0001)'

    def add_arguments(self, parser):
        parser.add_argument(
            'excel_file',
            type=str,
            help='Path to vendor Excel file',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Dry run - show what would be imported without saving',
        )
        parser.add_argument(
            '--user-email',
            type=str,
            default='admin@radai.ae',
            help='Email of user to set as created_by',
        )

    def handle(self, *args, **options):
        excel_file = options['excel_file']
        dry_run = options['dry_run']
        user_email = options['user_email']

        # Get or create admin user
        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(f'User {user_email} not found, using None'))
            user = None

        self.stdout.write(f'Reading Excel file: {excel_file}')
        
        try:
            # Read Excel with soft-coded skip rows
            df = pd.read_excel(excel_file, skiprows=HEADER_SKIP_ROWS)
            self.stdout.write(f'Total rows read: {len(df)}')
            
            # Filter out empty rows (soft-coded validation)
            df = df[df[EXCEL_COLUMN_MAP['vendor_name']].notna()]
            self.stdout.write(f'Valid vendor rows: {len(df)}')
            
            # Statistics
            created_count = 0
            updated_count = 0
            skipped_count = 0
            
            for index, row in df.iterrows():
                try:
                    vendor_name = str(row[EXCEL_COLUMN_MAP['vendor_name']]).strip()
                    
                    # Generate vendor code (soft-coded pattern)
                    vendor_code = self._generate_vendor_code(vendor_name, index + 1)
                    
                    # Prepare vendor data (soft-coded field mapping)
                    vendor_data = {
                        'name': vendor_name,
                        'vendor_code': vendor_code,
                        'contact_person': self._clean_value(row.get(EXCEL_COLUMN_MAP['contact_person'], '')),
                        'phone': self._clean_value(row.get(EXCEL_COLUMN_MAP['phone'], '')),
                        'email': self._clean_value(row.get(EXCEL_COLUMN_MAP['email'], '')),
                        'address': self._clean_value(row.get(EXCEL_COLUMN_MAP['address'], '')),
                        'trade_license_number': self._clean_value(row.get(EXCEL_COLUMN_MAP['trade_license_number'], '')),
                        'vat_number': self._clean_value(row.get(EXCEL_COLUMN_MAP['vat_number'], '')),
                        'notes': self._clean_value(row.get(EXCEL_COLUMN_MAP['notes'], '')),
                        
                        # Boolean fields (soft-coded Y/N parsing)
                        'is_icv_certified': self._parse_yes_no(row.get(EXCEL_COLUMN_MAP['is_icv_certified'], '')),
                        'adnoc_approved': self._parse_yes_no(row.get(EXCEL_COLUMN_MAP['adnoc_approved'], '')),
                        
                        # Integer field
                        'vendor_tenure_years': self._parse_int(row.get(EXCEL_COLUMN_MAP['vendor_tenure_years'], None)),
                        
                        # Category as JSONField list (soft-coded)
                        'categories': [self._clean_value(row.get(EXCEL_COLUMN_MAP['category'], ''))] if pd.notna(row.get(EXCEL_COLUMN_MAP['category'])) else [],
                        
                        # Defaults (soft-coded)
                        **DEFAULTS,
                    }
                    
                    if user:
                        vendor_data['created_by'] = user
                    
                    if dry_run:
                        self.stdout.write(f'Would create/update: {vendor_code} - {vendor_name}')
                        created_count += 1
                    else:
                        # Update or create (soft-coded upsert)
                        vendor, created = Vendor.objects.update_or_create(
                            vendor_code=vendor_code,
                            defaults=vendor_data
                        )
                        
                        if created:
                            created_count += 1
                            self.stdout.write(self.style.SUCCESS(f'✓ Created: {vendor_code} - {vendor_name}'))
                        else:
                            updated_count += 1
                            self.stdout.write(self.style.WARNING(f'↻ Updated: {vendor_code} - {vendor_name}'))
                
                except Exception as e:
                    skipped_count += 1
                    self.stdout.write(self.style.ERROR(f'✗ Error at row {index + HEADER_SKIP_ROWS + 2}: {str(e)}'))
            
            # Summary (soft-coded output)
            self.stdout.write('\n' + '='*60)
            self.stdout.write(self.style.SUCCESS('IMPORT SUMMARY'))
            self.stdout.write('='*60)
            self.stdout.write(f'Total rows processed: {len(df)}')
            self.stdout.write(self.style.SUCCESS(f'Created: {created_count}'))
            self.stdout.write(self.style.WARNING(f'Updated: {updated_count}'))
            if skipped_count > 0:
                self.stdout.write(self.style.ERROR(f'Skipped (errors): {skipped_count}'))
            
            if dry_run:
                self.stdout.write(self.style.NOTICE('\n[DRY RUN] No changes were saved to database'))
            else:
                self.stdout.write(self.style.SUCCESS(f'\n✓ Import completed successfully'))
        
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f'File not found: {excel_file}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Import failed: {str(e)}'))
    
    def _generate_vendor_code(self, vendor_name, index):
        """
        Generate vendor code from vendor name
        Soft-coded pattern: First 3 letters + index padded to 4 digits
        """
        # Remove special characters and take first 3 letters
        clean_name = ''.join(c for c in vendor_name.upper() if c.isalpha())
        prefix = clean_name[:3] if len(clean_name) >= 3 else clean_name.ljust(3, 'X')
        
        # Pad index to 4 digits
        suffix = str(index).zfill(4)
        
        return f'{prefix}-{suffix}'
    
    def _clean_value(self, value):
        """Clean and normalize string values (soft-coded)"""
        if pd.isna(value):
            return ''
        return str(value).strip()
    
    def _parse_yes_no(self, value):
        """Parse Y/N values to boolean (soft-coded)"""
        if pd.isna(value):
            return False
        
        value_str = str(value).strip().upper()
        return value_str in ['Y', 'YES', '1', 'TRUE']
    
    def _parse_int(self, value):
        """Parse integer values safely (soft-coded)"""
        if pd.isna(value):
            return None
        
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None
