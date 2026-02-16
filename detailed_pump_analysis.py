"""
Detailed analysis of Pump Data Sheet Excel files
Extracts actual form fields with proper structure
"""
import sys
from pathlib import Path
import json
import pandas as pd

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BLUE = '\033[94m'
RESET = '\033[0m'

def detailed_sheet_analysis(file_path, file_name):
    """Detailed analysis extracting actual field labels and structure"""
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}DETAILED ANALYSIS: {file_name}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    
    try:
        excel_file = pd.ExcelFile(file_path)
        all_fields = []
        
        for sheet_name in excel_file.sheet_names:
            print(f"\n{GREEN}{'='*80}{RESET}")
            print(f"{GREEN}[SHEET]: {sheet_name}{RESET}")
            print(f"{GREEN}{'='*80}{RESET}\n")
            
            # Read without header to preserve structure
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
            
            print(f"{CYAN}Complete Sheet Data:{RESET}\n")
            
            # Print all non-empty cells with their positions
            for row_idx in range(len(df)):
                row = df.iloc[row_idx]
                row_data = []
                
                for col_idx in range(len(row)):
                    cell = row.iloc[col_idx]
                    if pd.notna(cell) and str(cell).strip():
                        value = str(cell).strip()
                        row_data.append(f"[{col_idx}]: {value}")
                        
                        # Identify potential form fields
                        # Look for patterns like "Field Name:" or "Field Name"
                        if ':' in value or len(value.split()) <= 5:
                            all_fields.append({
                                'sheet': sheet_name,
                                'row': row_idx,
                                'col': col_idx,
                                'label': value,
                                'type': 'text'  # default
                            })
                
                if row_data:
                    print(f"Row {row_idx:3d}: {' | '.join(row_data)}")
            
            print(f"\n{CYAN}Sheet dimensions: {len(df)} rows × {len(df.columns)} columns{RESET}")
        
        return all_fields
        
    except Exception as e:
        print(f"{RED}[ERROR]: {e}{RESET}")
        import traceback
        traceback.print_exc()
        return []

def main():
    """Main execution"""
    temp_dir = Path('temp_pump_analysis')
    
    if not temp_dir.exists():
        print(f"{RED}Error: temp_pump_analysis directory not found{RESET}")
        print("Run analyze_pump_sheets.py first")
        return 1
    
    files = [
        {
            'path': temp_dir / 'pump_hydraulic_calculation_input.xlsx',
            'name': 'INPUT: Pump Hydraulic Calculation'
        },
        {
            'path': temp_dir / 'pump_data_sheet_output.xlsx',
            'name': 'OUTPUT: Pump Data Sheet'
        }
    ]
    
    all_analysis = {}
    
    for file_info in files:
        if file_info['path'].exists():
            fields = detailed_sheet_analysis(file_info['path'], file_info['name'])
            all_analysis[file_info['name']] = fields
            print(f"\n{GREEN}[SUCCESS] Found {len(fields)} potential fields{RESET}")
        else:
            print(f"{RED}[ERROR] File not found: {file_info['path']}{RESET}")
    
    # Save detailed analysis
    output_file = 'pump_detailed_analysis.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_analysis, f, indent=2, ensure_ascii=False)
    
    print(f"\n{GREEN}{'='*80}{RESET}")
    print(f"{GREEN}[COMPLETE] Detailed analysis saved to: {output_file}{RESET}")
    print(f"{GREEN}{'='*80}{RESET}\n")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
