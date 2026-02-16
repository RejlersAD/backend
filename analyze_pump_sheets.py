"""
Download and analyze Pump Hydraulic Calculation files from S3
Soft-coded approach using environment configuration
"""
import sys
import os
from pathlib import Path
import json

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from decouple import config
    import boto3
    import pandas as pd
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    sys.exit(1)

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BLUE = '\033[94m'
RESET = '\033[0m'

def get_s3_client():
    """Get S3 client using soft-coded credentials"""
    return boto3.client(
        's3',
        aws_access_key_id=config('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=config('AWS_SECRET_ACCESS_KEY'),
        region_name=config('AWS_S3_REGION_NAME', default='me-central-1')
    )

def download_s3_file(bucket_name, s3_key, local_path):
    """Download file from S3"""
    try:
        s3_client = get_s3_client()
        print(f"{CYAN}Downloading: {s3_key}{RESET}")
        s3_client.download_file(bucket_name, s3_key, local_path)
        print(f"{GREEN}✓ Downloaded to: {local_path}{RESET}")
        return True
    except ClientError as e:
        print(f"{RED}✗ Error downloading {s3_key}: {e}{RESET}")
        return False

def analyze_excel_structure(file_path, file_type="Input"):
    """Analyze Excel file structure and extract all fields"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}Analyzing {file_type}: {Path(file_path).name}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")
    
    try:
        # Read all sheets
        excel_file = pd.ExcelFile(file_path)
        print(f"{CYAN}Sheets found: {len(excel_file.sheet_names)}{RESET}")
        
        analysis = {
            'file': str(file_path),
            'type': file_type,
            'sheets': []
        }
        
        for sheet_name in excel_file.sheet_names:
            print(f"\n{GREEN}📋 Sheet: {sheet_name}{RESET}")
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
            
            sheet_data = {
                'name': sheet_name,
                'rows': len(df),
                'cols': len(df.columns),
                'fields': []
            }
            
            # Extract field structure
            print(f"   Dimensions: {len(df)} rows × {len(df.columns)} columns")
            
            # Look for label-value pairs (common in datasheets)
            for idx, row in df.iterrows():
                row_data = []
                for col_idx, cell in enumerate(row):
                    if pd.notna(cell) and str(cell).strip():
                        row_data.append({
                            'row': idx,
                            'col': col_idx,
                            'value': str(cell).strip()
                        })
                
                if row_data:
                    sheet_data['fields'].append(row_data)
            
            analysis['sheets'].append(sheet_data)
            
            # Display first 20 rows for understanding
            print(f"\n   {CYAN}First 20 rows preview:{RESET}")
            for idx, row in df.head(20).iterrows():
                non_empty = [str(cell) for cell in row if pd.notna(cell) and str(cell).strip()]
                if non_empty:
                    print(f"   Row {idx:2d}: {' | '.join(non_empty[:5])}")
        
        return analysis
        
    except Exception as e:
        print(f"{RED}✗ Error analyzing file: {e}{RESET}")
        import traceback
        traceback.print_exc()
        return None

def extract_form_fields(input_analysis, output_analysis):
    """Extract all form fields from input sheet"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}EXTRACTING FORM FIELDS{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")
    
    form_structure = {
        'sections': [],
        'total_fields': 0
    }
    
    # Process input sheet to identify all fields
    if input_analysis and input_analysis['sheets']:
        for sheet in input_analysis['sheets']:
            print(f"{GREEN}Processing sheet: {sheet['name']}{RESET}")
            
            section = {
                'name': sheet['name'],
                'fields': []
            }
            
            # Identify field patterns (label in one cell, value in adjacent cell)
            for field_row in sheet['fields']:
                if len(field_row) >= 2:
                    # Potential label-value pair
                    label = field_row[0]['value']
                    # Check if it looks like a label (not a number, not too long)
                    if len(label) < 100 and not label.replace('.', '').replace(',', '').isdigit():
                        section['fields'].append({
                            'label': label,
                            'row': field_row[0]['row'],
                            'col': field_row[0]['col']
                        })
            
            form_structure['sections'].append(section)
            form_structure['total_fields'] += len(section['fields'])
            
            print(f"   Found {len(section['fields'])} potential fields")
    
    return form_structure

def main():
    """Main execution"""
    bucket_name = 'process-department'
    
    files_to_download = [
        {
            'key': 'Pump_Hydralic_Calculation.xlsx',
            'local': 'pump_hydraulic_calculation_input.xlsx',
            'type': 'Input Data Sheet'
        },
        {
            'key': 'Pump Data Sheet.xlsx',
            'local': 'pump_data_sheet_output.xlsx',
            'type': 'Output Template'
        }
    ]
    
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}PUMP HYDRAULIC CALCULATION - FILE ANALYSIS{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")
    
    # Create temp directory
    temp_dir = Path('temp_pump_analysis')
    temp_dir.mkdir(exist_ok=True)
    
    analyses = {}
    
    # Download and analyze files
    for file_info in files_to_download:
        local_path = temp_dir / file_info['local']
        
        if download_s3_file(bucket_name, file_info['key'], str(local_path)):
            analysis = analyze_excel_structure(local_path, file_info['type'])
            analyses[file_info['type']] = analysis
    
    # Extract form structure
    if 'Input Data Sheet' in analyses and 'Output Template' in analyses:
        form_fields = extract_form_fields(
            analyses['Input Data Sheet'],
            analyses['Output Template']
        )
        
        # Save analysis
        output_file = 'pump_form_structure.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'input_analysis': analyses['Input Data Sheet'],
                'output_analysis': analyses['Output Template'],
                'form_fields': form_fields
            }, f, indent=2, ensure_ascii=False)
        
        print(f"\n{GREEN}{'='*70}{RESET}")
        print(f"{GREEN}✅ ANALYSIS COMPLETE{RESET}")
        print(f"{GREEN}{'='*70}{RESET}\n")
        print(f"   Total fields identified: {form_fields['total_fields']}")
        print(f"   Sections: {len(form_fields['sections'])}")
        print(f"   Analysis saved to: {output_file}")
        print(f"\n   Files are in: {temp_dir}/")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
