"""
Test script to verify report configuration is loaded correctly
Run: python test_report_config.py
"""
import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

django.setup()

from django.conf import settings
from apps.pid_analysis.export_service import PIDReportExportService


def test_report_configuration():
    """Test that report configuration is loaded properly"""
    
    print("=" * 60)
    print("REPORT CONFIGURATION TEST")
    print("=" * 60)
    print()
    
    # Test settings values
    print("📋 Django Settings Values:")
    print(f"  REPORT_COMPANY_NAME: {getattr(settings, 'REPORT_COMPANY_NAME', 'NOT SET')}")
    print(f"  REPORT_COMPANY_SUBTITLE: {getattr(settings, 'REPORT_COMPANY_SUBTITLE', 'NOT SET')}")
    print(f"  REPORT_COMPANY_WEBSITE: {getattr(settings, 'REPORT_COMPANY_WEBSITE', 'NOT SET')}")
    print(f"  REPORT_PRIMARY_COLOR: {getattr(settings, 'REPORT_PRIMARY_COLOR', 'NOT SET')}")
    print(f"  REPORT_SECONDARY_COLOR: {getattr(settings, 'REPORT_SECONDARY_COLOR', 'NOT SET')}")
    print(f"  REPORT_TITLE: {getattr(settings, 'REPORT_TITLE', 'NOT SET')}")
    print(f"  REPORT_FOOTER_TEXT: {getattr(settings, 'REPORT_FOOTER_TEXT', 'NOT SET')}")
    print()
    
    # Test export service initialization
    print("🔧 Export Service Configuration:")
    try:
        export_service = PIDReportExportService()
        print(f"  ✅ Service initialized successfully")
        print(f"  Company Name: {export_service.company_name}")
        print(f"  Company Subtitle: {export_service.company_subtitle}")
        print(f"  Company Website: {export_service.company_website}")
        print(f"  Report Title: {export_service.report_title}")
        print(f"  Footer Text: {export_service.footer_text}")
        print()
        print("  Colors:")
        for color_name, color_value in export_service.colors.items():
            print(f"    {color_name}: {color_value}")
        print()
        print("✅ All configuration loaded successfully!")
        
    except Exception as e:
        print(f"  ❌ Error initializing service: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 60)
    print("Configuration Status:")
    
    # Check if using defaults or environment variables
    using_defaults = getattr(settings, 'REPORT_COMPANY_NAME', None) == 'REJLERS ABU DHABI'
    if using_defaults:
        print("⚠️  Using default configuration (no environment variables set)")
        print("💡 To customize, set environment variables in .env file")
    else:
        print("✅ Using custom configuration from environment variables")
    
    print("=" * 60)


if __name__ == '__main__':
    test_report_configuration()
