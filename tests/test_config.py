"""
Backend Test Configuration Module
Provides unified configuration for backend testing
Matches frontend test.config.js
"""

import os
from pathlib import Path
from typing import Dict, Any

# Import from main test config
import sys
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

try:
    from config.test_config import (
        URLs, TestCredentials, Timeouts, APIEndpoints,
        FeatureFlags, DatabaseConfig, UploadConfig, CURRENT_ENV
    )
except ImportError:
    print("⚠️  Warning: Could not import main test_config. Using fallback configuration.")
    
    class URLs:
        FRONTEND_LOCAL = "http://localhost:5173"
        BACKEND_LOCAL = "http://localhost:8000"
        API_LOCAL = f"{BACKEND_LOCAL}/api/v1"
    
    class TestCredentials:
        EMAIL = "tanzeem.agra@rejlers.ae"
        PASSWORD = "Tanzeem@123"
    
    class Timeouts:
        SHORT = 5
        MEDIUM = 10
        LONG = 30
    
    class APIEndpoints:
        AUTH_LOGIN = "/auth/login/"
        USERS_ME = "/rbac/users/me/"
        PFD_UPLOAD = "/pfd/documents/upload/"
    
    CURRENT_ENV = {
        'frontend': URLs.FRONTEND_LOCAL,
        'backend': URLs.BACKEND_LOCAL,
        'api': URLs.API_LOCAL
    }

# Export all configurations
__all__ = [
    'URLs',
    'TestCredentials',
    'Timeouts',
    'APIEndpoints',
    'FeatureFlags',
    'DatabaseConfig',
    'UploadConfig',
    'CURRENT_ENV'
]

# Test configuration getter
def get_test_config() -> Dict[str, Any]:
    """Get complete test configuration as dictionary"""
    return {
        'urls': {
            'frontend': CURRENT_ENV['frontend'],
            'backend': CURRENT_ENV['backend'],
            'api': CURRENT_ENV['api']
        },
        'credentials': {
            'email': TestCredentials.EMAIL,
            'password': TestCredentials.PASSWORD
        },
        'timeouts': {
            'short': Timeouts.SHORT,
            'medium': Timeouts.MEDIUM,
            'long': Timeouts.LONG
        },
        'endpoints': {
            'auth_login': APIEndpoints.AUTH_LOGIN,
            'users_me': APIEndpoints.USERS_ME,
            'pfd_upload': APIEndpoints.PFD_UPLOAD
        }
    }

# Print configuration on import (debugging)
if os.getenv('DEBUG', 'false').lower() == 'true':
    print("=" * 60)
    print("BACKEND TEST CONFIGURATION LOADED")
    print("=" * 60)
    config = get_test_config()
    for key, value in config.items():
        print(f"{key.upper()}:")
        if isinstance(value, dict):
            for k, v in value.items():
                print(f"  - {k}: {v}")
        else:
            print(f"  {value}")
    print("=" * 60)
