#!/usr/bin/env python
"""Check PO_Generated.xlsx for General PR data"""
import pandas as pd
import os

project_root = r'c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow'
file_path = os.path.join(project_root, 'Documents', 'Procurement', 'PO_Generated.xlsx')

print(f"Reading: {file_path}")

xl = pd.ExcelFile(file_path)
print(f"\n📋 Sheets: {xl.sheet_names}\n")

for sheet in xl.sheet_names:
    print(f"\n{'='*80}")
    print(f"SHEET: {sheet}")
    print(f"{'='*80}")
    
    df = pd.read_excel(file_path, sheet_name=sheet)
    print(f"Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    
    # Check for PR columns
    pr_cols = [col for col in df.columns if 'pr' in str(col).lower() or 'requisition' in str(col).lower()]
    if pr_cols:
        print(f"\n✅ PR-related columns: {pr_cols}")
        for col in pr_cols:
            print(f"\n  Column: {col}")
            print(f"  Sample values:")
            for i, val in enumerate(df[col].dropna().head(20), 1):
                print(f"    {i:2d}. {val}")
    
    # Also search for RAD-GEN-PR in any column
    found_gen_pr = False
    for col in df.columns:
        if df[col].astype(str).str.contains('RAD-GEN-PR', case=False, na=False).any():
            found_gen_pr = True
            print(f"\n✅ Found RAD-GEN-PR in column: {col}")
            gen_prs = df[col][df[col].astype(str).str.contains('RAD-GEN-PR', case=False, regex=True)]
            print(f"  Total: {len(gen_prs)}")
            for i, val in enumerate(gen_prs.head(20), 1):
                print(f"    {i:2d}. {val}")
            break
    
    if not found_gen_pr:
        print(f"\n⚠️  No RAD-GEN-PR found in this sheet")
        # Show first few rows
        print(f"\nFirst 5 rows:")
        print(df.head(5).to_string())
