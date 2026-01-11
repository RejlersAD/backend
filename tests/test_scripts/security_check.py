#!/usr/bin/env python3
"""
Security Validation Script - Pre-Deployment Checks
Ensures no secrets are exposed in codebase before deployment
"""

import os
import re
import sys
from pathlib import Path

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

class SecurityValidator:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.issues_found = []
        self.warnings_found = []
        
    def print_header(self, message):
        print(f"\n{BLUE}{'='*70}{RESET}")
        print(f"{BLUE}{message:^70}{RESET}")
        print(f"{BLUE}{'='*70}{RESET}\n")
        
    def print_success(self, message):
        print(f"{GREEN}✅ {message}{RESET}")
        
    def print_error(self, message):
        print(f"{RED}❌ {message}{RESET}")
        self.issues_found.append(message)
        
    def print_warning(self, message):
        print(f"{YELLOW}⚠️  {message}{RESET}")
        self.warnings_found.append(message)
        
    def check_aws_keys(self):
        """Check for hardcoded AWS access keys"""
        self.print_header("Checking for AWS Access Keys")
        
        patterns = [
            (r'AKIA[0-9A-Z]{16}', 'AWS Access Key ID'),
            (r'(?i)aws_access_key_id\s*=\s*["\'][^"\']+["\']', 'AWS Access Key assignment'),
            (r'(?i)aws_secret_access_key\s*=\s*["\'][^"\']+["\']', 'AWS Secret Key assignment'),
        ]
        
        python_files = list(self.base_dir.rglob('*.py'))
        
        for file_path in python_files:
            if 'venv' in str(file_path) or 'env' in str(file_path):
                continue
                
            try:
                content = file_path.read_text()
                for pattern, description in patterns:
                    if re.search(pattern, content):
                        self.print_error(f"Found {description} in {file_path}")
            except Exception as e:
                self.print_warning(f"Could not read {file_path}: {e}")
                
        if not self.issues_found:
            self.print_success("No AWS keys found in code")
            
    def check_secret_keys(self):
        """Check for hardcoded Django secret keys"""
        self.print_header("Checking for Django SECRET_KEY")
        
        patterns = [
            r'SECRET_KEY\s*=\s*["\'][^"\']{20,}["\']',
            r'(?i)secret.*key.*=.*["\'][^"\']+["\']',
        ]
        
        settings_files = list(self.base_dir.rglob('settings*.py'))
        
        for file_path in settings_files:
            try:
                content = file_path.read_text()
                for pattern in patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        # Exclude config() calls (safe)
                        if 'config(' not in match.group(0):
                            self.print_error(f"Found hardcoded SECRET_KEY in {file_path}")
                            self.print_error(f"  Line: {match.group(0)[:50]}...")
            except Exception as e:
                self.print_warning(f"Could not read {file_path}: {e}")
                
        if not self.issues_found:
            self.print_success("No hardcoded SECRET_KEY found")
            
    def check_api_keys(self):
        """Check for API keys (OpenAI, etc.)"""
        self.print_header("Checking for API Keys")
        
        patterns = [
            (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API Key'),
            (r'(?i)openai_api_key\s*=\s*["\']sk-[^"\']+["\']', 'OpenAI Key assignment'),
            (r'(?i)api_key\s*=\s*["\'][^"\']{20,}["\']', 'Generic API Key'),
        ]
        
        python_files = list(self.base_dir.rglob('*.py'))
        
        for file_path in python_files:
            if 'venv' in str(file_path) or 'env' in str(file_path):
                continue
                
            try:
                content = file_path.read_text()
                for pattern, description in patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        if 'config(' not in match.group(0):
                            self.print_error(f"Found {description} in {file_path}")
            except Exception as e:
                pass
                
        if not self.issues_found:
            self.print_success("No API keys found in code")
            
    def check_passwords(self):
        """Check for hardcoded passwords"""
        self.print_header("Checking for Hardcoded Passwords")
        
        patterns = [
            r'(?i)password\s*=\s*["\'][^"\']+["\']',
            r'(?i)db_password\s*=\s*["\'][^"\']+["\']',
        ]
        
        python_files = list(self.base_dir.rglob('*.py'))
        
        for file_path in python_files:
            if 'venv' in str(file_path):
                continue
                
            try:
                content = file_path.read_text()
                for pattern in patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        # Exclude safe patterns
                        if 'config(' not in match.group(0) and \
                           'default=' not in match.group(0) and \
                           'password_field' not in match.group(0).lower():
                            self.print_error(f"Found hardcoded password in {file_path}")
                            self.print_error(f"  Line: {match.group(0)[:50]}...")
            except Exception as e:
                pass
                
        if not self.issues_found:
            self.print_success("No hardcoded passwords found")
            
    def check_env_files(self):
        """Check if .env files are properly gitignored"""
        self.print_header("Checking .env Files")
        
        env_files = list(self.base_dir.rglob('.env*'))
        
        if env_files:
            self.print_warning(f"Found {len(env_files)} .env file(s)")
            for env_file in env_files:
                self.print_warning(f"  {env_file}")
            self.print_warning("Ensure these are in .gitignore!")
        else:
            self.print_success("No .env files found in repository")
            
        # Check .gitignore
        gitignore = self.base_dir / '.gitignore'
        if gitignore.exists():
            content = gitignore.read_text()
            if '.env' in content:
                self.print_success(".gitignore contains .env pattern")
            else:
                self.print_error(".gitignore missing .env pattern")
        else:
            self.print_error(".gitignore file not found")
            
    def check_dockerfile_secrets(self):
        """Check Dockerfile for secrets in ARG/ENV"""
        self.print_header("Checking Dockerfile for Secrets")
        
        dockerfile_patterns = [
            (r'(?i)ARG\s+(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|SECRET_KEY|DB_PASSWORD|OPENAI_API_KEY)', 
             'ARG with secret'),
            (r'(?i)ENV\s+(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|SECRET_KEY|DB_PASSWORD|OPENAI_API_KEY)\s*=',
             'ENV with secret'),
        ]
        
        dockerfiles = list(self.base_dir.rglob('Dockerfile*'))
        
        for dockerfile in dockerfiles:
            try:
                content = dockerfile.read_text()
                for pattern, description in dockerfile_patterns:
                    if re.search(pattern, content):
                        self.print_error(f"Found {description} in {dockerfile}")
            except Exception as e:
                pass
                
        if not self.issues_found:
            self.print_success("Dockerfile is clean (no secret ARG/ENV)")
            
    def check_requirements_file(self):
        """Validate requirements.txt exists and is readable"""
        self.print_header("Checking requirements.txt")
        
        req_file = self.base_dir / 'requirements.txt'
        
        if req_file.exists():
            self.print_success("requirements.txt found")
            try:
                content = req_file.read_text()
                packages = [line.split('==')[0] for line in content.split('\n') if line and not line.startswith('#')]
                self.print_success(f"Found {len(packages)} packages")
            except Exception as e:
                self.print_error(f"Could not read requirements.txt: {e}")
        else:
            self.print_error("requirements.txt not found")
            
    def run_all_checks(self):
        """Run all security checks"""
        print(f"\n{BLUE}{'='*70}{RESET}")
        print(f"{BLUE}{'SECURITY VALIDATION - PRE-DEPLOYMENT CHECKS':^70}{RESET}")
        print(f"{BLUE}{'='*70}{RESET}")
        
        self.check_aws_keys()
        self.check_secret_keys()
        self.check_api_keys()
        self.check_passwords()
        self.check_env_files()
        self.check_dockerfile_secrets()
        self.check_requirements_file()
        
        # Summary
        self.print_header("VALIDATION SUMMARY")
        
        if self.issues_found:
            print(f"\n{RED}{'='*70}{RESET}")
            print(f"{RED}CRITICAL: {len(self.issues_found)} security issue(s) found!{RESET}")
            print(f"{RED}{'='*70}{RESET}")
            print(f"\n{RED}❌ DEPLOYMENT BLOCKED - Fix issues before deploying{RESET}\n")
            return False
        elif self.warnings_found:
            print(f"\n{YELLOW}{'='*70}{RESET}")
            print(f"{YELLOW}⚠️  {len(self.warnings_found)} warning(s) found{RESET}")
            print(f"{YELLOW}{'='*70}{RESET}")
            print(f"\n{YELLOW}Review warnings before deploying{RESET}\n")
            return True
        else:
            print(f"\n{GREEN}{'='*70}{RESET}")
            print(f"{GREEN}✅ ALL CHECKS PASSED - SAFE TO DEPLOY{RESET}")
            print(f"{GREEN}{'='*70}{RESET}\n")
            return True

if __name__ == '__main__':
    # Get script directory
    script_dir = Path(__file__).parent
    
    # Run validation
    validator = SecurityValidator(script_dir)
    success = validator.run_all_checks()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)
