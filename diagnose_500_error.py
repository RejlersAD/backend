"""
Diagnose 500 errors on spec_customization endpoints in production.
Run this with production DATABASE_URL to see what's failing.
"""
import os
import sys
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.spec_customization.models import PaperSpecExtractionJob
from apps.spec_customization.project_models import SpecProject
from apps.spec_customization.serializers import PaperSpecExtractionJobBriefSerializer

User = get_user_model()

print("=" * 70)
print("PRODUCTION 500 ERROR DIAGNOSTIC")
print("=" * 70)
print()

# Test 1: Check if models can be queried
print("Test 1: Model Query Test")
print("-" * 70)
try:
    project_count = SpecProject.objects.count()
    job_count = PaperSpecExtractionJob.objects.count()
    print(f"  ✅ SpecProject count: {project_count}")
    print(f"  ✅ PaperSpecExtractionJob count: {job_count}")
except Exception as e:
    print(f"  ❌ ERROR querying models: {e}")
    traceback.print_exc()
print()

# Test 2: Check if cost tracking fields exist
print("Test 2: Cost Tracking Fields")
print("-" * 70)
try:
    if job_count > 0:
        job = PaperSpecExtractionJob.objects.first()
        
        # Try to access cost tracking fields
        fields_to_check = [
            ('gemini_prompt_tokens', lambda: job.gemini_prompt_tokens),
            ('gemini_completion_tokens', lambda: job.gemini_completion_tokens),
            ('openai_prompt_tokens', lambda: job.openai_prompt_tokens),
            ('openai_completion_tokens', lambda: job.openai_completion_tokens),
            ('cost_usd', lambda: job.cost_usd),
            ('engineer_name', lambda: job.engineer_name),
            ('user_openai_api_key', lambda: job.user_openai_api_key),
        ]
        
        for field_name, accessor in fields_to_check:
            try:
                value = accessor()
                print(f"  ✅ {field_name:30s} = {value}")
            except AttributeError:
                print(f"  ❌ {field_name:30s} - FIELD MISSING (migration not applied)")
            except Exception as e:
                print(f"  ⚠️  {field_name:30s} - ERROR: {e}")
    else:
        print("  ⚠️  No jobs in database - skipping field check")
except Exception as e:
    print(f"  ❌ ERROR: {e}")
    traceback.print_exc()
print()

# Test 3: Test annotation query (like list_project_jobs does)
print("Test 3: Annotation Query")
print("-" * 70)
try:
    from django.db.models import Count
    
    # Try the same query that list_project_jobs uses
    jobs_qs = PaperSpecExtractionJob.objects.all()[:5]
    
    print("  Testing annotation...")
    annotated_qs = jobs_qs.annotate(
        classes_count=Count('piping_classes', distinct=True),
        components_count=Count('piping_classes__components', distinct=True),
    )
    
    jobs = list(annotated_qs)
    print(f"  ✅ Annotation successful - retrieved {len(jobs)} jobs")
    
    for job in jobs[:2]:
        print(f"    Job {str(job.id)[:8]}: "
              f"classes={getattr(job, 'classes_count', 'N/A')}, "
              f"components={getattr(job, 'components_count', 'N/A')}")
    
except Exception as e:
    print(f"  ❌ Annotation FAILED: {e}")
    print(f"     This is likely causing the 500 error!")
    traceback.print_exc()
print()

# Test 4: Test serializer
print("Test 4: Serializer Test")
print("-" * 70)
try:
    if job_count > 0:
        job = PaperSpecExtractionJob.objects.select_related('document', 'created_by').first()
        
        # Manually add annotations like the view does
        job.classes_count = 0
        job.components_count = 0
        
        print("  Testing serializer...")
        serializer = PaperSpecExtractionJobBriefSerializer(job)
        data = serializer.data
        
        print(f"  ✅ Serialization successful")
        print(f"    Fields: {list(data.keys())}")
        print(f"    Sample data:")
        for key in ['id', 'status', 'user_name', 'cost_usd', 'gemini_prompt_tokens']:
            if key in data:
                print(f"      {key:25s}: {data[key]}")
    else:
        print("  ⚠️  No jobs in database - skipping serializer test")
except Exception as e:
    print(f"  ❌ Serialization FAILED: {e}")
    print(f"     This is likely causing the 500 error!")
    traceback.print_exc()
print()

# Test 5: Simulate the actual endpoint
print("Test 5: Simulate list_project_jobs Endpoint")
print("-" * 70)
try:
    if project_count > 0:
        project = SpecProject.objects.first()
        print(f"  Using project: {project.name} ({project.project_id})")
        
        # Simulate the exact query from list_project_jobs
        pid_str = str(project.project_id)
        jobs_qs = (
            PaperSpecExtractionJob.objects
            .filter(document__project_id=pid_str)
            .select_related('document', 'created_by')
        )
        
        # Try annotation
        try:
            from django.db.models import Count as DjangoCount
            jobs_qs = jobs_qs.annotate(
                classes_count=DjangoCount('piping_classes', distinct=True),
                components_count=DjangoCount('piping_classes__components', distinct=True),
            )
            print("  ✅ Annotation successful")
        except Exception as ann_err:
            print(f"  ⚠️  Annotation failed: {ann_err}")
            print("     (This is expected if migrations not applied - code should handle it)")
        
        jobs_qs = jobs_qs.order_by('-created_at')
        total = jobs_qs.count()
        jobs = list(jobs_qs[:5])
        
        print(f"  ✅ Query successful - {total} jobs total, retrieved {len(jobs)}")
        
        # Try serialization
        serializer = PaperSpecExtractionJobBriefSerializer(jobs, many=True)
        data = serializer.data
        
        print(f"  ✅ Serialization successful - {len(data)} jobs serialized")
        
        if data:
            print(f"    First job fields: {list(data[0].keys())}")
    else:
        print("  ⚠️  No projects in database - skipping endpoint simulation")
except Exception as e:
    print(f"  ❌ ENDPOINT SIMULATION FAILED: {e}")
    print(f"     THIS IS THE CAUSE OF THE 500 ERROR!")
    print()
    print("  Full traceback:")
    traceback.print_exc()
print()

print("=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
print()
print("If you see ❌ errors above, those are the root cause of the 500 errors.")
print("Most common issues:")
print("  1. Migration 0005/0006 not applied → fields missing")
print("  2. Annotation failing → relationship issue")
print("  3. Serializer failing → field access error")
print()
print("Solution:")
print("  Run: python manage.py migrate spec_customization")
print("  Or: Trigger Railway redeploy")
print("=" * 70)
