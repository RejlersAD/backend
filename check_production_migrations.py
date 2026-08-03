"""
Check if spec_customization migrations 0005 and 0006 are applied in production.
Run this with production DATABASE_URL.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

print("=" * 70)
print("PRODUCTION MIGRATION CHECK")
print("=" * 70)
print()

# Check applied migrations
applied = MigrationRecorder.Connection(connection).applied_migrations()

spec_migrations = [
    ('spec_customization', '0004_add_component_matching_models'),
    ('spec_customization', '0005_rename_spec_cust_pa_sha256__idx_spec_custom_sha256__ce399b_idx_and_more'),
    ('spec_customization', '0006_add_byok_fields'),
]

print("Spec Customization Migrations:")
print()
for app, migration in spec_migrations:
    is_applied = (app, migration) in applied
    status = "✅ APPLIED" if is_applied else "❌ MISSING"
    print(f"  {status}  {migration}")
print()

# Check if columns exist in database
print("Database Column Check:")
print()
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'spec_customization_paperspecextractionjob'
        AND column_name IN (
            'gemini_prompt_tokens', 
            'gemini_completion_tokens',
            'openai_prompt_tokens',
            'openai_completion_tokens',
            'cost_usd',
            'engineer_name',
            'user_openai_api_key'
        )
        ORDER BY column_name;
    """)
    existing_cols = [row[0] for row in cursor.fetchall()]

required_cols = [
    'gemini_prompt_tokens',       # from 0005
    'gemini_completion_tokens',   # from 0005
    'openai_prompt_tokens',       # from 0005
    'openai_completion_tokens',   # from 0005
    'cost_usd',                   # from 0005
    'engineer_name',              # from 0006
    'user_openai_api_key',        # from 0006
]

for col in required_cols:
    exists = col in existing_cols
    status = "✅ EXISTS" if exists else "❌ MISSING"
    migration = "0005" if col in ['gemini_prompt_tokens', 'gemini_completion_tokens', 
                                   'openai_prompt_tokens', 'openai_completion_tokens', 
                                   'cost_usd'] else "0006"
    print(f"  {status}  {col:30s} (from migration {migration})")

print()
print("=" * 70)
if len(existing_cols) == len(required_cols):
    print("✅ All columns exist - migrations applied successfully!")
else:
    print("❌ MIGRATIONS NEEDED!")
    print()
    print("To fix:")
    print("  1. SSH into Railway production instance")
    print("  2. Run: python manage.py migrate spec_customization")
    print("  3. Or trigger Railway redeploy to auto-run migrations")
print("=" * 70)
