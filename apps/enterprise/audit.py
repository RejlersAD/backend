"""Writes Team & Enterprise events to the existing rbac.AuditLog table."""
import ipaddress
import logging

logger = logging.getLogger(__name__)


def _trusted_proxy_hops():
    import os
    from django.conf import settings
    value = getattr(settings, 'ENTERPRISE_TRUSTED_PROXY_COUNT', None)
    if value is None:
        value = os.environ.get('ENTERPRISE_TRUSTED_PROXY_COUNT', '0')
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def client_ip(request):
    """The caller's IP. Uses X-Forwarded-For only for the configured number of trusted proxy hops
    (ENTERPRISE_TRUSTED_PROXY_COUNT, default 0 = the direct connection), so it can't be spoofed."""
    if request is None:
        return None
    value = (request.META.get('REMOTE_ADDR') or '').strip()
    hops = _trusted_proxy_hops()
    forwarded = [part.strip() for part in (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',') if part.strip()]
    if hops and forwarded:
        value = forwarded[-hops] if len(forwarded) >= hops else forwarded[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def audit(request, action, resource_type, org, resource_repr='', metadata=None, success=True):
    """Never raises: an audit failure must not break the user's action, but it is logged."""
    from apps.rbac.models import AuditLog
    actor = getattr(request, 'user', None)
    try:
        AuditLog.objects.create(
            user=actor if getattr(actor, 'is_authenticated', False) else None,
            user_email=(getattr(actor, 'email', '') or '')[:254],
            action=action,
            resource_type=resource_type,
            resource_repr=str(resource_repr)[:255],
            ip_address=client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:500],
            metadata={'module': 'enterprise', 'organization_id': str(org.pk), **(metadata or {})},
            success=success,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception('Enterprise audit write failed for %s %s', action, resource_type)
