#!/usr/bin/env python
"""Verify P&ID Checker V2 database tables"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

cursor = connection.cursor()
cursor.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'pidv2_%' ORDER BY tablename")
tables = cursor.fetchall()

print("\n" + "="*60)
print("P&ID CHECKER V2 — DATABASE TABLES VERIFICATION")
print("="*60 + "\n")

for idx, (table,) in enumerate(tables, 1):
    print(f"{idx:2d}. {table}")

print(f"\n{'='*60}")
print(f"Total V2 tables: {len(tables)}/12 expected")
print("="*60 + "\n")

if len(tables) == 12:
    print("✅ SUCCESS: All 12 V2 tables created!")
    sys.exit(0)
else:
    print(f"❌ WARNING: Expected 12 tables, found {len(tables)}")
    sys.exit(1)
