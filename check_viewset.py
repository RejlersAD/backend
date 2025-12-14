import sys
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from apps.pid_analysis.views import PIDDrawingViewSet

print("Checking PIDDrawingViewSet methods...")
methods = [m for m in dir(PIDDrawingViewSet) if not m.startswith('_')]
print(f"\nTotal methods: {len(methods)}")

# Check for export
if 'export' in methods:
    print("✓ export method found")
    export_method = getattr(PIDDrawingViewSet, 'export')
    print(f"  Method: {export_method}")
    if hasattr(export_method, 'mapping'):
        print(f"  HTTP methods: {export_method.mapping}")
    if hasattr(export_method, 'url_path'):
        print(f"  URL path: {export_method.url_path}")
else:
    print("✗ export method NOT found")
    
print("\nAll methods:")
for m in sorted(methods):
    print(f"  - {m}")
