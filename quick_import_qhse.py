"""
Quick QHSE Data Import Script
Handles Excel files with headers at different rows
"""
import pandas as pd
import sys
import os
import django

# Setup Django
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.qhse.models import QHSERunningProject
from django.db import transaction
from datetime import datetime

print("\n" + "="*70)
print("🔄 QHSE Quick Import Tool")
print("="*70 + "\n")

# Read Excel with correct header row
print("📂 Reading Excel file...")
df = pd.read_excel('/app/qhse_data.xlsx', sheet_name='QHSE _Running Project Status ', header=2)

print(f"📊 Found {len(df)} rows")
print(f"📋 Columns: {list(df.columns)[:5]}...\n")

# Field mapping for all columns
field_map = {
    'Sr No': 'sr_no',
    'Project No': 'project_no',
    'Project Title': 'project_title',
    'CLIENT': 'client',
    'Project Manager': 'project_manager',
    'Project Starting Date': 'project_starting_date',
    'Project Closing Date': 'project_closing_date',
    'Project Extension': 'project_extension',
    'Project Quality Engineer': 'project_quality_eng',
    'Manhours for Quality': 'man_hour_for_quality',
    'Manhours Used': 'manhours_used',
    'Manhours Balance': 'manhours_balance',
    'Quality Billability                       %': 'quality_billability_percent',
    'Project Quality Plan status - Rev': 'project_quality_plan_status_rev',
    'Project Quality Plan status -                     Issued Date': 'project_quality_plan_status_issue_date',
    'Project Audit -1': 'project_audit_1',
    'Project Audit -2': 'project_audit_2',
    'Project Audit -3': 'project_audit_3',
    'Project Audit -4': 'project_audit_4',
    'Client Audit -1': 'client_audit_1',
    'Client Audit -2': 'client_audit_2',
    'Delay in Audits - No. of Days': 'delay_in_audits_no_days',
    'CARs Open': 'cars_open',
    'CARs Delayed closing            No. days': 'cars_delayed_closing_no_days',
    'CARs Closed': 'cars_closed',
    'No. of Obs Open': 'obs_open',
    'Obs delayed closing  No. of Days': 'obs_delayed_closing_no_days',
    'Obs Closed': 'obs_closed',
    'Project KPIs Achieved   %': 'project_kpis_achieved_percent',
    'Project Compl.   %': 'project_completion_percent',
    'Rejection of Deliverables %': 'rejection_of_deliverables_percent',
    'Cost of Poor Quality in AED': 'cost_of_poor_quality_aed',
    'Remarks': 'remarks'
}

created = 0
updated = 0
skipped = 0

with transaction.atomic():
    for idx, row in df.iterrows():
        try:
            # Skip rows without project number
            project_no = str(row.get('Project No', '')).strip()
            if pd.isna(row.get('Project No')) or not project_no or project_no == 'nan':
                continue
            
            # Basic string fields
            data = {
                'sr_no': int(idx + 1),
                'project_no': project_no,
                'project_title': str(row.get('Project Title', '')),
                'client': str(row.get('CLIENT', '')),
                'project_manager': str(row.get('Project Manager', '')),
                'project_quality_eng': str(row.get('Project Quality Engineer', '')),
                'project_quality_plan_status_rev': str(row.get('Project Quality Plan status - Rev', '')),
                'remarks': str(row.get('Remarks', ''))
            }
            
            # Date fields
            date_fields = {
                'Project Starting Date': 'project_starting_date',
                'Project Closing Date': 'project_closing_date',
                'Project Extension': 'project_extension',
                'Project Quality Plan status -                     Issued Date': 'project_quality_plan_status_issue_date',
                'Project Audit -1': 'project_audit_1',
                'Project Audit -2': 'project_audit_2',
                'Project Audit -3': 'project_audit_3',
                'Project Audit -4': 'project_audit_4',
                'Client Audit -1': 'client_audit_1',
                'Client Audit -2': 'client_audit_2',
            }
            
            for excel_col, db_field in date_fields.items():
                val = row.get(excel_col)
                if pd.notna(val) and val != '':
                    try:
                        if isinstance(val, datetime):
                            data[db_field] = val.date()
                        else:
                            data[db_field] = pd.to_datetime(val).date()
                    except:
                        data[db_field] = None
                else:
                    data[db_field] = None
            
            # Numeric fields (integers)
            num_int_fields = {
                'Manhours for Quality': 'man_hour_for_quality',
                'Manhours Used': 'manhours_used',
                'Manhours Balance': 'manhours_balance',
                'Delay in Audits - No. of Days': 'delay_in_audits_no_days',
                'CARs Open': 'cars_open',
                'CARs Delayed closing            No. days': 'cars_delayed_closing_no_days',
                'CARs Closed': 'cars_closed',
                'No. of Obs Open': 'obs_open',
                'Obs delayed closing  No. of Days': 'obs_delayed_closing_no_days',
                'Obs Closed': 'obs_closed',
                'Cost of Poor Quality in AED': 'cost_of_poor_quality_aed'
            }
            
            for excel_col, db_field in num_int_fields.items():
                val = row.get(excel_col)
                try:
                    data[db_field] = int(float(val)) if pd.notna(val) and val != '' else 0
                except:
                    data[db_field] = 0
            
            # Percentage fields (store as strings like "85%")
            percent_fields = {
                'Quality Billability                       %': 'quality_billability_percent',
                'Project KPIs Achieved   %': 'project_kpis_achieved_percent',
                'Project Compl.   %': 'project_completion_percent',
                'Rejection of Deliverables %': 'rejection_of_deliverables_percent'
            }
            
            for excel_col, db_field in percent_fields.items():
                val = row.get(excel_col)
                if pd.notna(val) and val != '':
                    try:
                        # If it's already a string with %, keep it
                        if isinstance(val, str) and '%' in val:
                            data[db_field] = val
                        else:
                            # Convert to percentage string
                            num_val = float(val)
                            # If value is between 0 and 1, treat as decimal (0.85 → 85%)
                            if 0 <= num_val <= 1:
                                data[db_field] = f"{int(num_val * 100)}%"
                            else:
                                # Otherwise treat as percentage already (85 → 85%)
                                data[db_field] = f"{int(num_val)}%"
                    except:
                        data[db_field] = "0%"
                else:
                    data[db_field] = "0%"
            
            # Create or update
            project, is_created = QHSERunningProject.objects.update_or_create(
                project_no=data['project_no'],
                defaults=data
            )
            
            if is_created:
                created += 1
                print(f"  ✅ Created: {project.project_no}")
            else:
                updated += 1
                print(f"  🔄 Updated: {project.project_no}")
        
        except Exception as e:
            skipped += 1
            print(f"  ⚠️  Skipped row {idx + 1}: {str(e)}")

print(f"\n📈 Import Summary:")
print(f"  • Created: {created}")
print(f"  • Updated: {updated}")
print(f"  • Skipped: {skipped}")
print("\n✅ Import completed!\n")
