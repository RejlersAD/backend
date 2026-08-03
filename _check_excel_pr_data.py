#!/usr/bin/env python
"""
Quick script to check Purchase Requisition data in Excel file
"""
import pandas as pd
import sys

# Read Excel file
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
file_path = os.path.join(project_root, 'Documents', 'Procurement', 'PO_PR_Data.xlsx')

try:
    # Get all sheet names
    xl_file = pd.ExcelFile(file_path)
    sheets = xl_file.sheet_names
    print(f"📋 Sheets in Excel file: {sheets}\n")
    
    # Read each sheet
    for sheet_name in sheets:
        print(f"\n{'='*80}")
        print(f"SHEET: {sheet_name}")
        print(f"{'='*80}")
        
        # Read with header row
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        print(f"Rows: {len(df)}")
        print(f"Columns: {list(df.columns)[:5]}...")  # First 5 columns
        
        # Try to find PR numbers
        for col in df.columns:
            if df[col].astype(str).str.contains('RAD-.*-PR-', case=False, na=False).any():
                pr_numbers = df[col].dropna().astype(str)
                pr_numbers = pr_numbers[pr_numbers.str.contains('RAD-.*-PR-', case=False, regex=True)]
                print(f"\n✅ Found PR numbers in column '{col}':")
                print(f"Total PR records: {len(pr_numbers)}")
                print("\nFirst 20 PR numbers:")
                for i, pr in enumerate(pr_numbers.head(20), 1):
                    print(f"  {i:2d}. {pr}")
                break
        
        print("\n")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
