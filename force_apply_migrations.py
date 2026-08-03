#!/usr/bin/env python
"""
Force-apply spec_customization migrations in production.
Run this in Railway shell: python force_apply_migrations.py
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command
from django.db import connection

print("=" * 70)
print("FORCE APPLY SPEC_CUSTOMIZATION MIGRATIONS")
print("=" * 70)
print()

# Step 1: Show current migration status
print("Step 1: Current Migration Status")
print("-" * 70)
try:
    call_command('showmigrations', 'spec_customization', verbosity=1)
except Exception as e:
    print(f"ERROR: Could not show migrations - {e}")
print()

# Step 2: Apply migrations
print("Step 2: Applying Migrations")
print("-" * 70)
try:
    call_command('migrate', 'spec_customization', verbosity=2)
    print("SUCCESS: Migrations applied")
except Exception as e:
    print(f"ERROR: Migration failed - {e}")
    import traceback
    traceback.print_exc()
print()

# Step 3: Verify columns exist
print("Step 3: Verifying Database Schema")
print("-" * 70)
try:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public'
            AND table_name = 'spec_customization_paperspecextractionjob'
            AND column_name IN (
                'gemini_prompt_tokens',
                'gemini_completion_tokens',
                'openai_prompt_tokens',
                'openai_completion_tokens',
                'cost_usd',
                'engineer_name',
                'user_openai_api_key'
            )
            ORDER BY column_name
        """)
        existing = [row[0] for row in cursor.fetchall()]
        
        required = [
            'gemini_prompt_tokens',
            'gemini_completion_tokens',
            'openai_prompt_tokens',
            'openai_completion_tokens',
            'cost_usd',
            'engineer_name',
            'user_openai_api_key',
        ]
        
        print("Column verification:")
        all_ok = True
        for col in required:
            if col in existing:
                print(f"  OK: {col}")
            else:
                print(f"  MISSING: {col}")
                all_ok = False
        
        print()
        if all_ok:
            print("SUCCESS: All required columns exist!")
        else:
            print("ERROR: Some columns still missing. Migration may have failed.")
            
except Exception as e:
    print(f"ERROR: Could not verify schema - {e}")
print()

# Step 4: Test endpoint functionality
print("Step 4: Testing Endpoint Functionality")
print("-" * 70)
try:
    from apps.spec_customization.models import PaperSpecExtractionJob
    from apps.spec_customization.serializers import PaperSpecExtractionJobBriefSerializer
    from django.db.models import Count
    
    # Try the query that list_project_jobs uses
    jobs = (
        PaperSpecExtractionJob.objects
        .select_related('document', 'created_by')
        .annotate(
            classes_count=Count('piping_classes', distinct=True),
            components_count=Count('piping_classes__components', distinct=True),
        )
    )[:1]
    
    job_list = list(jobs)
    print(f"  OK: Query successful - {len(job_list)} jobs retrieved")
    
    if job_list:
        # Try serialization
        serializer = PaperSpecExtractionJobBriefSerializer(job_list[0])
        data = serializer.data
        print(f"  OK: Serialization successful - {len(data)} fields")
        print(f"      Cost tracking: {data.get('cost_usd', 'N/A')}")
    
    print()
    print("SUCCESS: Endpoints should work now!")
    
except Exception as e:
    print(f"ERROR: Functionality test failed - {e}")
    import traceback
    traceback.print_exc()
print()

print("=" * 70)
print("MIGRATION COMPLETE")
print("=" * 70)
print()
print("Next steps:")
print("1. Test production: https://www.radai.ae/engineering/digitization/spec-customization")
print("2. Upload should work without 500 errors")
print("3. Job history should load successfully")
print("=" * 70)
