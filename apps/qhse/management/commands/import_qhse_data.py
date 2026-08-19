"""
Management Command: Import QHSE Data from Excel
Soft-coded to handle various Excel formats and field mappings
Usage: python manage.py import_qhse_data <excel_file_path>
"""
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from datetime import datetime
from apps.qhse.models import QHSERunningProject, QHSESpotCheckRegister


class Command(BaseCommand):
    help = 'Import QHSE data from Excel file to PostgreSQL database'
    
    # Soft-coded field mapping configuration
    RUNNING_PROJECTS_FIELD_MAPPING = {
        'Sr No': 'sr_no',
        'Project No': 'project_no',
        'Project Title': 'project_title',
        'Project Title Key': 'project_title_key',
        'Client': 'client',
        'Project Manager': 'project_manager',
        'Project Starting Date': 'project_starting_date',
        'Project Closing Date': 'project_closing_date',
        'Project Extension': 'project_extension',
        'Project Quality Engineer': 'project_quality_eng',
        'Manhours for Quality': 'man_hour_for_quality',
        'Manhours Used': 'manhours_used',
        'Manhours Balance': 'manhours_balance',
        'Quality Billability %': 'quality_billability_percent',
        'Project Quality Plan status - Rev': 'project_quality_plan_status_rev',
        'Project Quality Plan status - Issued Date': 'project_quality_plan_status_issue_date',
        'Project Audit -1': 'project_audit_1',
        'Project Audit -2': 'project_audit_2',
        'Project Audit -3': 'project_audit_3',
        'Project Audit -4': 'project_audit_4',
        'Client Audit -1': 'client_audit_1',
        'Client Audit -2': 'client_audit_2',
        'Delay in Audits - No. of Days': 'delay_in_audits_no_days',
        'CARs Open': 'cars_open',
        'CARs Delayed closing No. days': 'cars_delayed_closing_no_days',
        'CARs Closed': 'cars_closed',
        'No. of Obs Open': 'obs_open',
        'Obs delayed closing No. of Days': 'obs_delayed_closing_no_days',
        'Obs Closed': 'obs_closed',
        'Project KPIs Achieved %': 'project_kpis_achieved_percent',
        'Project Compl. %': 'project_completion_percent',
        'Rejection of Deleverables %': 'rejection_of_deliverables_percent',
        'Cost of Poor Quality               in AED': 'cost_of_poor_quality_aed',
        'Remarks': 'remarks'
    }
    
    SPOT_CHECK_FIELD_MAPPING = {
        'Sr No': 'sr_no',
        'Project No': 'project_no',
        'Project Title': 'project_title',
        'Client': 'client',
        'QHSE Engineer': 'qhse_engineer',
        'Date of Spot check': 'date_of_spot_check',
        'Time': 'time',
        'Document No.': 'document_no',
        'Document Title': 'document_title',
        'Originator / Lead': 'originator_lead',
        'Comments': 'comments',
        'Category': 'category',
        'Remarks': 'remarks'
    }
    
    def add_arguments(self, parser):
        parser.add_argument('excel_file', type=str, help='Path to Excel file')
        parser.add_argument(
            '--sheet',
            type=str,
            default='QHSE running projects status',
            help='Sheet name to import (default: QHSE running projects status)'
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['projects', 'spot_checks', 'both'],
            default='both',
            help='Type of data to import'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before import'
        )
    
    def handle(self, *args, **options):
        excel_file = options['excel_file']
        sheet_name = options['sheet']
        data_type = options['type']
        clear_existing = options['clear']
        
        self.stdout.write(self.style.SUCCESS(
            '\n' + '='*70 + '\n'
            '🔄 QHSE Data Import Tool\n'
            '' + '='*70 + '\n'
        ))
        
        try:
            # Read Excel file
            self.stdout.write(f'📂 Reading Excel file: {excel_file}')
            
            if data_type in ['projects', 'both']:
                self.import_running_projects(excel_file, sheet_name, clear_existing)
            
            if data_type in ['spot_checks', 'both']:
                self.import_spot_checks(excel_file, 'Spot Check Register', clear_existing)
            
            self.stdout.write(self.style.SUCCESS(
                '\n✅ Import completed successfully!\n'
            ))
            
        except Exception as e:
            raise CommandError(f'❌ Import failed: {str(e)}')
    
    @transaction.atomic
    def import_running_projects(self, excel_file, sheet_name, clear_existing):
        """Import Running Projects data"""
        self.stdout.write(f'\n📊 Importing Running Projects from sheet: {sheet_name}')
        
        # Read sheet
        df = pd.read_excel(excel_file, sheet_name=sheet_name)
        
        if clear_existing:
            self.stdout.write(self.style.WARNING('🗑️  Clearing existing Running Projects data...'))
            QHSERunningProject.objects.all().delete()
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for index, row in df.iterrows():
            try:
                # Skip empty rows
                if pd.isna(row.get('Sr No')):
                    continue
                
                # Prepare data dictionary
                data = self.prepare_project_data(row)
                
                # Check if project exists
                project, created = QHSERunningProject.objects.update_or_create(
                    project_no=data['project_no'],
                    defaults=data
                )
                
                if created:
                    created_count += 1
                    self.stdout.write(f'  ✅ Created: {project.project_no}')
                else:
                    updated_count += 1
                    self.stdout.write(f'  🔄 Updated: {project.project_no}')
                    
            except Exception as e:
                skipped_count += 1
                self.stdout.write(self.style.WARNING(
                    f'  ⚠️  Skipped row {index + 2}: {str(e)}'
                ))
        
        self.stdout.write(self.style.SUCCESS(
            f'\n📈 Running Projects Import Summary:\n'
            f'  • Created: {created_count}\n'
            f'  • Updated: {updated_count}\n'
            f'  • Skipped: {skipped_count}\n'
        ))
    
    @transaction.atomic
    def import_spot_checks(self, excel_file, sheet_name, clear_existing):
        """Import Spot Check Register data"""
        self.stdout.write(f'\n📋 Importing Spot Check Register from sheet: {sheet_name}')
        
        try:
            df = pd.read_excel(excel_file, sheet_name=sheet_name)
        except:
            self.stdout.write(self.style.WARNING(
                f'⚠️  Sheet "{sheet_name}" not found, skipping spot checks import'
            ))
            return
        
        if clear_existing:
            self.stdout.write(self.style.WARNING('🗑️  Clearing existing Spot Check data...'))
            QHSESpotCheckRegister.objects.all().delete()
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for index, row in df.iterrows():
            try:
                # Skip empty rows
                if pd.isna(row.get('Sr No')):
                    continue
                
                # Prepare data dictionary
                data = self.prepare_spot_check_data(row)
                
                # Create spot check (allow duplicates by sr_no)
                spot_check = QHSESpotCheckRegister.objects.create(**data)
                created_count += 1
                self.stdout.write(f'  ✅ Created: Spot Check #{spot_check.sr_no}')
                    
            except Exception as e:
                skipped_count += 1
                self.stdout.write(self.style.WARNING(
                    f'  ⚠️  Skipped row {index + 2}: {str(e)}'
                ))
        
        self.stdout.write(self.style.SUCCESS(
            f'\n📈 Spot Check Import Summary:\n'
            f'  • Created: {created_count}\n'
            f'  • Skipped: {skipped_count}\n'
        ))
    
    def prepare_project_data(self, row):
        """Prepare project data from Excel row"""
        data = {}
        
        for excel_col, model_field in self.RUNNING_PROJECTS_FIELD_MAPPING.items():
            value = row.get(excel_col)
            
            # Handle different data types
            if pd.isna(value) or value == '' or str(value).lower() in ['n/a', 'not applicable', 'na']:
                if model_field in ['remarks', 'project_title_key', 'rejection_of_deliverables_percent',
                                   'project_quality_plan_status_rev', 'project_audit_1', 'project_audit_2',
                                   'project_audit_3', 'project_audit_4', 'client_audit_1', 'client_audit_2',
                                   'project_extension', 'project_quality_plan_status_issue_date']:
                    data[model_field] = None
                elif model_field in ['man_hour_for_quality', 'manhours_used', 'manhours_balance', 
                                     'cost_of_poor_quality_aed']:
                    data[model_field] = 0
                elif model_field in ['delay_in_audits_no_days', 'cars_open', 'cars_delayed_closing_no_days',
                                     'cars_closed', 'obs_open', 'obs_delayed_closing_no_days', 'obs_closed']:
                    data[model_field] = 0
                elif model_field in ['quality_billability_percent', 'project_kpis_achieved_percent',
                                     'project_completion_percent']:
                    data[model_field] = '0%'
                continue
            
            # Convert dates
            if 'date' in model_field and value and not pd.isna(value):
                if isinstance(value, datetime):
                    data[model_field] = value.date()
                else:
                    data[model_field] = pd.to_datetime(value, errors='coerce')
                    if pd.isna(data[model_field]):
                        data[model_field] = None
                    else:
                        data[model_field] = data[model_field].date()
            # Handle percentage fields
            elif model_field in ['quality_billability_percent', 'project_kpis_achieved_percent', 
                                 'project_completion_percent', 'rejection_of_deliverables_percent']:
                data[model_field] = str(value) if '%' in str(value) else f"{value}%"
            # Handle numeric fields
            elif model_field in ['sr_no', 'delay_in_audits_no_days', 'cars_open', 
                                 'cars_delayed_closing_no_days', 'cars_closed', 'obs_open', 
                                 'obs_delayed_closing_no_days', 'obs_closed']:
                data[model_field] = int(float(value)) if value else 0
            elif model_field in ['man_hour_for_quality', 'manhours_used', 'manhours_balance', 
                                 'cost_of_poor_quality_aed']:
                data[model_field] = float(value) if value else 0
            else:
                data[model_field] = str(value) if value else ''
        
        return data
    
    def prepare_spot_check_data(self, row):
        """Prepare spot check data from Excel row"""
        data = {}
        
        for excel_col, model_field in self.SPOT_CHECK_FIELD_MAPPING.items():
            value = row.get(excel_col)
            
            if pd.isna(value) or value == '':
                if model_field in ['time', 'document_no', 'document_title', 'originator_lead', 
                                   'comments', 'category', 'remarks']:
                    data[model_field] = None
                continue
            
            # Convert date
            if model_field == 'date_of_spot_check':
                if isinstance(value, datetime):
                    data[model_field] = value.date()
                else:
                    data[model_field] = pd.to_datetime(value, errors='coerce').date()
            # Handle time
            elif model_field == 'time':
                if isinstance(value, datetime):
                    data[model_field] = value.time()
                else:
                    data[model_field] = None
            # Handle category mapping
            elif model_field == 'category':
                category_map = {
                    'OBSERVATION': 'OBSERVATION',
                    'CAR': 'CAR',
                    'NCR': 'NCR',
                    'COMPLIANT': 'COMPLIANT',
                    'MINOR': 'MINOR',
                    'MAJOR': 'MAJOR',
                }
                data[model_field] = category_map.get(str(value).upper(), str(value).upper())
            elif model_field == 'sr_no':
                data[model_field] = int(float(value))
            else:
                data[model_field] = str(value)
        
        # Set default status if not present
        if 'status' not in data:
            data['status'] = 'OPEN'
        
        return data
