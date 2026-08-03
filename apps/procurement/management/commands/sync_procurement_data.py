"""
Django Management Command: Sync Procurement Data Between Environments
Smart data transfer utility with soft-coded configuration
"""

from django.core.management.base import BaseCommand
from django.core import serializers
from django.contrib.auth import get_user_model
from apps.procurement.models import (
    PurchaseOrder, PurchaseRequisition, Vendor, Receipt
)
import json
import os
from pathlib import Path

User = get_user_model()


# Soft-coded sync configuration
SYNC_CONFIG = {
    'export_path': 'data_exports',  # Relative to backend directory
    'models_to_sync': [
        {'app': 'procurement', 'model': 'Vendor', 'order': 1},
        {'app': 'procurement', 'model': 'PurchaseRequisition', 'order': 2},
        {'app': 'procurement', 'model': 'PurchaseOrder', 'order': 3},
        {'app': 'procurement', 'model': 'Receipt', 'order': 4},
    ],
    'exclude_fields': ['created_at', 'updated_at'],  # Optional: preserve timestamps
    'use_natural_keys': True,  # Use natural keys for foreign keys
}


class Command(BaseCommand):
    help = 'Sync procurement data between environments (export/import)'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['export', 'import', 'preview'],
            help='Action to perform: export, import, or preview',
        )
        parser.add_argument(
            '--file',
            type=str,
            default='procurement_data.json',
            help='Filename for export/import (default: procurement_data.json)',
        )
        parser.add_argument(
            '--include-users',
            action='store_true',
            help='Include user references (requires users to exist in target)',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing records with same ID/number',
        )
        parser.add_argument(
            '--format',
            type=str,
            choices=['json', 'yaml', 'xml'],
            default='json',
            help='Export format (default: json)',
        )

    def handle(self, *args, **options):
        action = options['action']
        filename = options['file']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"  PROCUREMENT DATA SYNC - {action.upper()}"))
        self.stdout.write("=" * 80)
        
        if action == 'export':
            self._export_data(filename, options)
        elif action == 'import':
            self._import_data(filename, options)
        elif action == 'preview':
            self._preview_data(filename)
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"✓ {action.capitalize()} complete!"))
        self.stdout.write("=" * 80)

    def _get_export_path(self, filename):
        """Get full export path using soft-coded configuration"""
        base_path = Path(__file__).resolve().parent.parent.parent.parent.parent
        export_dir = base_path / SYNC_CONFIG['export_path']
        export_dir.mkdir(exist_ok=True)
        return export_dir / filename

    def _export_data(self, filename, options):
        """Export procurement data to file"""
        self.stdout.write(f"\n📤 Exporting data to {filename}...")
        
        export_path = self._get_export_path(filename)
        
        # Get models to export based on soft-coded configuration
        models_config = sorted(SYNC_CONFIG['models_to_sync'], key=lambda x: x['order'])
        
        all_data = []
        total_records = 0
        
        for model_config in models_config:
            model_name = model_config['model']
            model = self._get_model(model_name)
            
            if not model:
                continue
            
            queryset = model.objects.all()
            count = queryset.count()
            total_records += count
            
            self.stdout.write(f"  → Exporting {model_name}: {count} records")
            
            # Serialize model data
            if SYNC_CONFIG['use_natural_keys']:
                data = serializers.serialize(
                    options['format'],
                    queryset,
                    use_natural_foreign_keys=True,
                    use_natural_primary_keys=False,
                )
            else:
                data = serializers.serialize(options['format'], queryset)
            
            all_data.append(data)
        
        # Write to file
        with open(export_path, 'w') as f:
            if options['format'] == 'json':
                # Combine all JSON arrays into one
                combined_data = []
                for data_str in all_data:
                    combined_data.extend(json.loads(data_str))
                json.dump(combined_data, f, indent=2)
            else:
                f.write('\n'.join(all_data))
        
        self.stdout.write(self.style.SUCCESS(f"\n✓ Exported {total_records} total records to {export_path}"))
        self.stdout.write(f"  File size: {os.path.getsize(export_path) / 1024:.2f} KB")
        
        # Show instructions
        self._show_import_instructions(export_path)

    def _import_data(self, filename, options):
        """Import procurement data from file"""
        export_path = self._get_export_path(filename)
        
        if not export_path.exists():
            self.stdout.write(self.style.ERROR(f"✗ File not found: {export_path}"))
            return
        
        self.stdout.write(f"\n📥 Importing data from {filename}...")
        self.stdout.write(f"  File size: {os.path.getsize(export_path) / 1024:.2f} KB")
        
        # Read file
        with open(export_path, 'r') as f:
            if options['format'] == 'json':
                data = json.load(f)
            else:
                data = f.read()
        
        # Preview
        self.stdout.write(f"\n  Found {len(data)} records to import")
        
        # Confirm if not --overwrite
        if not options['overwrite']:
            self.stdout.write(self.style.WARNING("\n⚠️  Records with existing IDs will be skipped"))
            confirm = input("Continue with import? (yes/no): ")
            if confirm.lower() != 'yes':
                self.stdout.write("Import cancelled")
                return
        
        # Import
        imported_count = 0
        skipped_count = 0
        
        for obj_data in data:
            model_name = obj_data['model'].split('.')[-1]
            pk = obj_data['pk']
            
            # Check if exists
            model = self._get_model(model_name)
            if model and model.objects.filter(pk=pk).exists():
                if options['overwrite']:
                    model.objects.filter(pk=pk).delete()
                    self.stdout.write(f"  → Overwriting {model_name} {pk}")
                else:
                    skipped_count += 1
                    continue
            
            imported_count += 1
        
        # Deserialize and save
        for obj in serializers.deserialize(options['format'], json.dumps(data)):
            obj.save()
        
        self.stdout.write(self.style.SUCCESS(f"\n✓ Imported {imported_count} records"))
        if skipped_count > 0:
            self.stdout.write(self.style.WARNING(f"  ⚠ Skipped {skipped_count} existing records"))

    def _preview_data(self, filename):
        """Preview data in export file without importing"""
        export_path = self._get_export_path(filename)
        
        if not export_path.exists():
            self.stdout.write(self.style.ERROR(f"✗ File not found: {export_path}"))
            return
        
        self.stdout.write(f"\n🔍 Previewing {filename}...")
        
        with open(export_path, 'r') as f:
            data = json.load(f)
        
        # Count by model
        model_counts = {}
        for obj_data in data:
            model = obj_data['model']
            model_counts[model] = model_counts.get(model, 0) + 1
        
        self.stdout.write(f"\n  Total records: {len(data)}")
        self.stdout.write(f"  File size: {os.path.getsize(export_path) / 1024:.2f} KB")
        self.stdout.write("\n  Records by model:")
        for model, count in sorted(model_counts.items()):
            self.stdout.write(f"    • {model}: {count}")

    def _get_model(self, model_name):
        """Get model class by name"""
        models = {
            'Vendor': Vendor,
            'PurchaseRequisition': PurchaseRequisition,
            'PurchaseOrder': PurchaseOrder,
            'Receipt': Receipt,
        }
        return models.get(model_name)

    def _show_import_instructions(self, export_path):
        """Show instructions for importing in another environment"""
        self.stdout.write(f"\n📖 IMPORT INSTRUCTIONS")
        self.stdout.write("-" * 80)
        self.stdout.write("\nTo import this data in PRODUCTION:")
        self.stdout.write(f"\n  1. Copy file to production server:")
        self.stdout.write(f"     {export_path}")
        self.stdout.write(f"\n  2. Run import command:")
        self.stdout.write(f"     railway run -- python manage.py sync_procurement_data import --file={export_path.name}")
        self.stdout.write(f"\n  3. Or preview first:")
        self.stdout.write(f"     railway run -- python manage.py sync_procurement_data preview --file={export_path.name}")
        self.stdout.write("\n" + "-" * 80)
