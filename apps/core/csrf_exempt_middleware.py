"""
CSRF Exemption Middleware for API Endpoints

This middleware exempts API endpoints from CSRF protection since they use
token-based authentication (JWT) instead of session-based authentication.

Soft-coded configuration via environment variables:
- CSRF_EXEMPT_PATHS: Comma-separated list of path patterns to exempt (default: /api/)
- CSRF_EXEMPT_ENABLED: Enable/disable CSRF exemption (default: True)
"""

import logging

import os
from django.utils.deprecation import MiddlewareMixin
logger = logging.getLogger(__name__)


class CsrfExemptMiddleware(MiddlewareMixin):
    """
    Middleware to exempt specific paths from CSRF validation.
    
    This is necessary for API endpoints that use token authentication
    instead of session-based authentication.
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response
        
        # Soft-coded configuration
        self.enabled = os.getenv('CSRF_EXEMPT_ENABLED', 'True').lower() in ('true', '1', 'yes')
        
        # Get exempt paths from environment (default: all /api/ paths)
        exempt_paths_str = os.getenv('CSRF_EXEMPT_PATHS', '/api/')
        self.exempt_paths = [p.strip() for p in exempt_paths_str.split(',') if p.strip()]
        
        logger.debug(f"[CsrfExemptMiddleware] Initialized")
        logger.debug(f"[CsrfExemptMiddleware] Enabled: {self.enabled}")
        logger.debug(f"[CsrfExemptMiddleware] Exempt paths: {self.exempt_paths}")
    
    def process_request(self, request):
        """
        Process incoming request and mark it as CSRF exempt if it matches exempt paths.
        """
        if not self.enabled:
            return None
        
        # Check if request path matches any exempt pattern
        for exempt_path in self.exempt_paths:
            if request.path.startswith(exempt_path):
                logger.debug(f"[CsrfExemptMiddleware] ✓ Exempting {request.path} from CSRF protection")
                # Mark request as CSRF exempt
                setattr(request, '_dont_enforce_csrf_checks', True)
                break
        
        return None
