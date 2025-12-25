"""
Django management command to test boto3 connectivity
Usage: python manage.py test_s3_connection
"""
from django.core.management.base import BaseCommand
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Test boto3 S3 connectivity and display connection status'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed connection information'
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Run troubleshooting diagnostics'
        )
    
    def handle(self, *args, **options):
        if options['fix']:
            self.run_troubleshooting()
        else:
            self.test_connection(detailed=options['detailed'])
    
    def test_connection(self, detailed=False):
        """Run connectivity test"""
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("AWS S3 CONNECTIVITY TEST"))
        self.stdout.write("="*60 + "\n")
        
        # Import test function
        try:
            from apps.core.boto3_helper import Boto3Helper
            
            # Run validation
            results = Boto3Helper.validate_connection()
            
            # Display results
            if results['boto3_installed']:
                self.stdout.write(self.style.SUCCESS(
                    f"✓ boto3 {results['details']['boto3_version']} installed"
                ))
            else:
                self.stdout.write(self.style.ERROR("✗ boto3 not installed"))
                return
            
            if results['credentials_configured']:
                self.stdout.write(self.style.SUCCESS(
                    f"✓ AWS credentials configured: {results['details']['access_key']}"
                ))
            else:
                self.stdout.write(self.style.ERROR("✗ AWS credentials not configured"))
                return
            
            # Main bucket
            main_bucket = results['details']['main_bucket']
            if results['main_bucket_accessible']:
                self.stdout.write(self.style.SUCCESS(
                    f"✓ Main bucket accessible: {main_bucket['name']} ({main_bucket['region']})"
                ))
                
                if detailed:
                    status = main_bucket['status']
                    self.stdout.write(f"  - Read permission: {status['has_read_permission']}")
                    self.stdout.write(f"  - Write permission: {status['has_write_permission']}")
            else:
                self.stdout.write(self.style.ERROR(
                    f"✗ Main bucket NOT accessible: {main_bucket['name']}"
                ))
                if main_bucket['status'].get('error'):
                    self.stdout.write(f"  Error: {main_bucket['status']['error']}")
            
            # PFD bucket
            pfd_bucket = results['details']['pfd_bucket']
            if results['pfd_bucket_accessible']:
                self.stdout.write(self.style.SUCCESS(
                    f"✓ PFD bucket accessible: {pfd_bucket['name']} ({pfd_bucket['region']})"
                ))
                
                if detailed:
                    status = pfd_bucket['status']
                    self.stdout.write(f"  - Read permission: {status['has_read_permission']}")
                    self.stdout.write(f"  - Write permission: {status['has_write_permission']}")
            else:
                self.stdout.write(self.style.WARNING(
                    f"⚠ PFD bucket NOT accessible: {pfd_bucket['name']}"
                ))
                if pfd_bucket['status'].get('error'):
                    self.stdout.write(f"  Error: {pfd_bucket['status']['error']}")
            
            # Connection info
            if detailed:
                self.stdout.write("\n" + "="*60)
                self.stdout.write("CONNECTION INFO")
                self.stdout.write("="*60)
                info = Boto3Helper.get_connection_info()
                self.stdout.write(f"Cached clients: {info['cached_clients']}")
                self.stdout.write(f"Cached resources: {info['cached_resources']}")
            
            # Summary
            self.stdout.write("\n" + "="*60)
            if results['main_bucket_accessible'] and results['pfd_bucket_accessible']:
                self.stdout.write(self.style.SUCCESS("✓ All S3 connections are working!"))
            elif results['main_bucket_accessible']:
                self.stdout.write(self.style.WARNING("⚠ Main bucket OK, PFD bucket has issues"))
            else:
                self.stdout.write(self.style.ERROR("✗ Connection issues detected"))
            self.stdout.write("="*60 + "\n")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error running test: {e}"))
    
    def run_troubleshooting(self):
        """Run troubleshooting diagnostics"""
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.WARNING("BOTO3 TROUBLESHOOTING"))
        self.stdout.write("="*60 + "\n")
        
        # Run the fix script
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        
        try:
            from fix_boto3_connection import check_and_fix_boto3
            check_and_fix_boto3()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
