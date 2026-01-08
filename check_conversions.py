import django
django.setup()

from apps.pfd_converter.models import PIDConversion

conversions = PIDConversion.objects.all().order_by('-created_at')[:10]

print("\n" + "="*80)
print("📋 LATEST P&ID CONVERSIONS IN DATABASE")
print("="*80)

if not conversions:
    print("\n❌ No P&ID conversions found in database")
else:
    for c in conversions:
        created_date = c.created_at.strftime('%Y-%m-%d %H:%M')
        file_path = c.pid_file if c.pid_file else 'No file'
        pipeline_version = c.design_parameters.get('pipeline_version', '1.0') if c.design_parameters else '1.0'
        
        print(f"\n{c.id}. {c.pid_drawing_number}")
        print(f"   Created: {created_date}")
        print(f"   Status: {c.status}")
        print(f"   Pipeline: v{pipeline_version}")
        print(f"   File: {file_path}")
        
        # Determine if it's old or new
        if pipeline_version == '2.1':
            print(f"   ✅ NEW AI-POWERED CONVERSION")
        elif pipeline_version == '2.0':
            print(f"   ⚠️  OLD ADVANCED PIPELINE (before AI)")
        else:
            print(f"   ❌ OLD BASIC CONVERSION")

print("\n" + "="*80)
print("\n💡 To see AI-generated drawings:")
print("   1. Upload a NEW PFD at http://localhost:5173/pfd/upload")
print("   2. Generate a NEW P&ID conversion")
print("   3. Download from the NEW conversion (pipeline v2.1)")
print("\n   Old conversions (v1.0, v2.0) will have old-style drawings")
print("="*80)
