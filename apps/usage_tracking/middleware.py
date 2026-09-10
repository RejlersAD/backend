"""
Usage Tracking Middleware
Logs every authenticated API request to UsageLog.
Very lightweight — skips noisy/system paths and never crashes the app.
"""
import time
from django.db import transaction
from django.utils import timezone

# Soft-coded: paths to skip (prefix match on request.path)
SKIP_PREFIXES = (
    '/api/v1/usage/',        # don't log our own analytics queries
    '/api/v1/health',
    '/api/v1/cors',
    '/api/v1/database',
    '/api/v1/system',
    '/api/schema/',
    '/api/docs/',
    '/static/',
    '/admin/',
    '/health/',
    '/media/',
)

# Only log proper API paths
API_PREFIX = '/api/'


class UsageTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)

        # Write log synchronously but guarded — adds ~1ms, never blocks
        try:
            self._log(request, response, start)
        except Exception:
            pass  # Never let tracking affect the response

        return response

    def _log(self, request, response, start):
        path = request.path

        # Only process API requests
        if not path.startswith(API_PREFIX):
            return

        # Skip noise paths
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return

        # Include authentication outcomes so failed logins and token requests
        # reach health telemetry, without recording credentials or request bodies.
        authenticated = bool(getattr(request, 'user', None) and request.user.is_authenticated)
        if not authenticated and not path.startswith('/api/v1/auth/'):
            return

        from .models import UsageLog, classify_discipline

        elapsed_ms = int((time.monotonic() - start) * 1000)
        discipline_key, discipline_label = classify_discipline(path)

        user = request.user if authenticated else None
        full_name = (f"{user.first_name} {user.last_name}".strip() or user.username) if user else ''

        # Isolate optional tracking writes so a missing/temporarily unavailable
        # analytics table cannot break the business transaction being served.
        with transaction.atomic():
            UsageLog.objects.create(
                user=user,
                user_email=(user.email or '') if user else '',
                user_full_name=full_name,
                discipline_key=discipline_key,
                discipline_label=discipline_label,
                request_path=path[:500],
                request_method=request.method,
                response_status=response.status_code,
                response_time_ms=elapsed_ms,
                success=response.status_code < 400,
                timestamp=timezone.now(),
            )
