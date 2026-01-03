#!/usr/bin/env python
"""
Environment Variable Validation Script for Railway Deployment
Run this before deployment to ensure all required variables are set.
"""

import os
import sys
from typing import List, Dict, Tuple

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


class EnvValidator:
    """Validates environment variables for Railway deployment"""
    
    REQUIRED_VARS = [
        'SECRET_KEY',
        'DATABASE_URL',
    ]
    
    RECOMMENDED_VARS = [
        'DEBUG',
        'ALLOWED_HOSTS',
        'CORS_ALLOWED_ORIGINS',
        'FRONTEND_URL',
        'BACKEND_URL',
        'PORT',
    ]
    
    EMAIL_VARS = [
        'EMAIL_BACKEND',
        'EMAIL_HOST',
        'EMAIL_PORT',
        'EMAIL_USE_TLS',
        'EMAIL_HOST_USER',
        'EMAIL_HOST_PASSWORD',
        'DEFAULT_FROM_EMAIL',
    ]
    
    AWS_VARS = [
        'USE_S3',
        'AWS_STORAGE_BUCKET_NAME',
        'AWS_S3_REGION_NAME',
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
    ]
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []
    
    def check_required(self) -> bool:
        """Check required environment variables"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Checking REQUIRED environment variables...{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        all_ok = True
        for var in self.REQUIRED_VARS:
            value = os.getenv(var)
            if not value:
                print(f"{RED}✗ {var}: MISSING (CRITICAL){RESET}")
                self.errors.append(f"Missing required variable: {var}")
                all_ok = False
            else:
                # Mask sensitive values
                display_value = value if var not in ['SECRET_KEY', 'DATABASE_URL'] else '***HIDDEN***'
                print(f"{GREEN}✓ {var}: {display_value}{RESET}")
        
        return all_ok
    
    def check_recommended(self) -> None:
        """Check recommended environment variables"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Checking RECOMMENDED environment variables...{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        for var in self.RECOMMENDED_VARS:
            value = os.getenv(var)
            if not value:
                print(f"{YELLOW}⚠ {var}: NOT SET (using default){RESET}")
                self.warnings.append(f"Recommended variable not set: {var}")
            else:
                print(f"{GREEN}✓ {var}: {value}{RESET}")
    
    def check_email(self) -> None:
        """Check email configuration"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Checking EMAIL configuration...{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        email_configured = False
        for var in self.EMAIL_VARS:
            value = os.getenv(var)
            if value:
                email_configured = True
                # Mask password
                display_value = value if var != 'EMAIL_HOST_PASSWORD' else '***HIDDEN***'
                print(f"{GREEN}✓ {var}: {display_value}{RESET}")
            else:
                print(f"{YELLOW}⚠ {var}: NOT SET{RESET}")
        
        if not email_configured:
            self.info.append("Email not configured - password reset will not work")
    
    def check_aws(self) -> None:
        """Check AWS S3 configuration"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Checking AWS S3 configuration...{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        use_s3 = os.getenv('USE_S3', 'False').lower() in ('true', '1', 'yes')
        
        if not use_s3:
            print(f"{YELLOW}⚠ USE_S3 is False - using local storage{RESET}")
            self.info.append("Using local storage (not S3)")
            return
        
        print(f"{GREEN}✓ USE_S3 is enabled{RESET}")
        
        for var in self.AWS_VARS[1:]:  # Skip USE_S3
            value = os.getenv(var)
            if value:
                # Mask sensitive values
                display_value = value if 'KEY' not in var else '***HIDDEN***'
                print(f"{GREEN}✓ {var}: {display_value}{RESET}")
            else:
                print(f"{RED}✗ {var}: MISSING{RESET}")
                self.errors.append(f"S3 enabled but missing: {var}")
    
    def check_django_settings(self) -> bool:
        """Try to load Django settings"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Testing Django settings import...{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        try:
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
            import django
            django.setup()
            print(f"{GREEN}✓ Django settings loaded successfully{RESET}")
            return True
        except Exception as e:
            print(f"{RED}✗ Failed to load Django settings: {e}{RESET}")
            self.errors.append(f"Django settings error: {str(e)}")
            return False
    
    def print_summary(self) -> bool:
        """Print validation summary"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}VALIDATION SUMMARY{RESET}")
        print(f"{BLUE}{'='*60}{RESET}\n")
        
        if self.errors:
            print(f"{RED}ERRORS ({len(self.errors)}):{RESET}")
            for error in self.errors:
                print(f"  {RED}✗ {error}{RESET}")
            print()
        
        if self.warnings:
            print(f"{YELLOW}WARNINGS ({len(self.warnings)}):{RESET}")
            for warning in self.warnings:
                print(f"  {YELLOW}⚠ {warning}{RESET}")
            print()
        
        if self.info:
            print(f"{BLUE}INFO ({len(self.info)}):{RESET}")
            for info_item in self.info:
                print(f"  {BLUE}ℹ {info_item}{RESET}")
            print()
        
        if not self.errors:
            print(f"{GREEN}{'='*60}{RESET}")
            print(f"{GREEN}✓ All required checks passed!{RESET}")
            print(f"{GREEN}{'='*60}{RESET}\n")
            return True
        else:
            print(f"{RED}{'='*60}{RESET}")
            print(f"{RED}✗ Validation failed - fix errors before deploying{RESET}")
            print(f"{RED}{'='*60}{RESET}\n")
            return False
    
    def run(self) -> bool:
        """Run all validation checks"""
        print(f"\n{GREEN}{'='*60}{RESET}")
        print(f"{GREEN}Railway Environment Validation{RESET}")
        print(f"{GREEN}{'='*60}{RESET}")
        
        # Run checks
        required_ok = self.check_required()
        self.check_recommended()
        self.check_email()
        self.check_aws()
        
        # Try to load Django if required vars are present
        if required_ok:
            self.check_django_settings()
        
        # Print summary
        return self.print_summary()


def main():
    """Main entry point"""
    validator = EnvValidator()
    success = validator.run()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
