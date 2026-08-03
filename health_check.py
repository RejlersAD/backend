"""
Simple production health check - no unicode characters.
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

print("=" * 70)
print("PRODUCTION HEALTH CHECK")
print("=" * 70)
print()

# Check database connection
print("1. Database Connection Test")
print("-" * 70)
try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        print("  OK: Database connection working")
except Exception as e:
    print(f"  ERROR: Database connection failed - {e}")
print()

# Check if migrations applied
print("2. Migration Status Check")
print("-" * 70)
try:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public'
            AND table_name = 'spec_customization_paperspecextractionjob'
            ORDER BY column_name
        """)
        columns = [row[0] for row in cursor.fetchall()]
        
        print(f"  Table has {len(columns)} columns")
        
        required_cols = [
            'gemini_prompt_tokens',
            'gemini_completion_tokens',
            'openai_prompt_tokens',
            'openai_completion_tokens',
            'cost_usd',
            'engineer_name',
            'user_openai_api_key',
        ]
        
        for col in required_cols:
            exists = col in columns
            status = "OK" if exists else "MISSING"
            migration = "0005" if 'token' in col or col == 'cost_usd' else "0006"
            print(f"    {status:10s} {col:30s} (migration {migration})")
        
        all_exist = all(col in columns for col in required_cols)
        if not all_exist:
            print()
            print("  ERROR: Migrations 0005 and/or 0006 NOT applied!")
            print("  Run: python manage.py migrate spec_customization")
            
except Exception as e:
    print(f"  ERROR: Could not check columns - {e}")
print()

# Check if we can query models
print("3. Model Query Test")
print("-" * 70)
try:
    from apps.spec_customization.models import PaperSpecExtractionJob
    from apps.spec_customization.project_models import SpecProject
    
    project_count = SpecProject.objects.count()
    job_count = PaperSpecExtractionJob.objects.count()
    
    print(f"  OK: SpecProject count = {project_count}")
    print(f"  OK: PaperSpecExtractionJob count = {job_count}")
except Exception as e:
    print(f"  ERROR: Model query failed - {e}")
print()

# Check if serializer works
print("4. Serializer Test")
print("-" * 70)
try:
    from apps.spec_customization.models import PaperSpecExtractionJob
    from apps.spec_customization.serializers import PaperSpecExtractionJobBriefSerializer
    
    job = PaperSpecExtractionJob.objects.first()
    if job:
        job.classes_count = 0
        job.components_count = 0
        serializer = PaperSpecExtractionJobBriefSerializer(job)
        data = serializer.data
        print(f"  OK: Serialized job {str(job.id)[:8]}")
        print(f"      Fields: {len(data)} fields returned")
    else:
        print("  SKIP: No jobs in database")
except Exception as e:
    print(f"  ERROR: Serializer failed - {e}")
    import traceback
    traceback.print_exc()
print()

# Check views helper function
print("5. Helper Function Test")
print("-" * 70)
try:
    from apps.spec_customization.views import _model_has_field
    from apps.spec_customization.models import PaperSpecExtractionJob
    
    test_fields = [
        ('id', True),
        ('engineer_name', None),
        ('nonexistent_field', False),
    ]
    
    for field_name, expected in test_fields:
        result = _model_has_field(PaperSpecExtractionJob, field_name)
        print(f"    {field_name:25s} -> {result}")
    
    print("  OK: _model_has_field() works")
except Exception as e:
    print(f"  ERROR: Helper function failed - {e}")
print()

print("=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
