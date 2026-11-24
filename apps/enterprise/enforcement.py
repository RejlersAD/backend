"""Runtime enforcement of the organization policy outside the Team & Enterprise screens.

Enforced here (Enterprise edition only, and only while ENABLE_ENTERPRISE_TEAMS is on):
  * approved AI models  -> filter_chat_models()
  * session idle timeout -> PolicyJWTAuthentication / check_idle()
  * allowed IP ranges    -> PolicyJWTAuthentication / check_ip()  (superusers are the break-glass)

Everything fails open when the feature is off, the user has no organization, or the
organization is below the Enterprise edition, so non-enterprise behaviour is unchanged.
"""
import ipaddress
import logging

from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

logger = logging.getLogger(__name__)

POLICY_CACHE_SECONDS = 60          # policy edits reach every user within a minute
ACTIVITY_WRITE_EVERY = 60          # write last-activity at most once a minute per user
ACTIVITY_TTL = 7 * 24 * 3600       # keep activity as long as a refresh token can live
BACKGROUND_HEADER = 'HTTP_X_RADAI_BACKGROUND'
IDLE_CODE = 'session_idle_timeout'
IP_CODE = 'ip_not_allowed'


def _policy_key(user_id):
    return f'enterprise:user-policy:{user_id}'


def _activity_key(user_id):
    return f'enterprise:last-activity:{user_id}'


def active_policy(user):
    """The enforced policy for the user's organization, or None. Cached briefly per user."""
    from django.apps import apps
    if not user or not getattr(user, 'is_authenticated', False) or not apps.is_installed('apps.enterprise'):
        return None
    from . import access as acl
    if not acl.enterprise_enabled():
        return None
    cached = cache.get(_policy_key(user.pk))
    if cached is not None:
        return cached or None
    value = {}
    try:
        org = acl.organization_for(user)
        if org and acl.effective_edition(org) == 'enterprise':
            policy = acl.policy_for(org)
            value = {
                'session_timeout_minutes': policy.session_timeout_minutes,
                'allowed_chat_models': list(policy.allowed_chat_models or []),
                'allowed_ip_ranges': list(policy.allowed_ip_ranges or []),
                'allow_web_search': bool(policy.allow_web_search),
            }
    except Exception:  # never let a policy lookup break the request
        logger.exception('Enterprise policy lookup failed; not enforcing for this request.')
        return None
    cache.set(_policy_key(user.pk), value, POLICY_CACHE_SECONDS)
    return value or None


# ---------------------------------------------------------------- approved AI models
def filter_chat_models(user, models):
    """Keep only models on the organization's approved list (an empty list approves all).

    Entries may be a bare model id ("claude-sonnet-4-6") or "provider:model" ("ollama:qwen3:8b").
    Returns (models, restricted) where restricted is True when the policy removed any model.
    """
    allowed = set((active_policy(user) or {}).get('allowed_chat_models') or [])
    if not allowed:
        return models, False
    kept = [row for row in models
            if row['model_id'] in allowed or f"{row['provider']}:{row['model_id']}" in allowed]
    return kept, len(kept) < len(models)


# ---------------------------------------------------------------- web search
def web_search_allowed(user):
    """Web search sends questions to the public internet, so it needs an explicit organization opt-in."""
    return bool((active_policy(user) or {}).get('allow_web_search'))


# ---------------------------------------------------------------- session idle timeout
def check_idle(user, background=False):
    """Raise AuthenticationFailed when the user has been inactive longer than the policy allows.

    Activity is the newest of: the last user-initiated request (cache) and the last login (DB),
    so a fresh login always starts a new session. Background polling never counts as activity.
    With no recorded activity yet (policy just switched on, or the cache was cleared) the idle
    clock starts now: people who are already signed in are never thrown out on the spot.
    """
    minutes = (active_policy(user) or {}).get('session_timeout_minutes')
    if not minutes:
        return
    now = timezone.now().timestamp()
    last_activity = cache.get(_activity_key(user.pk))
    if last_activity is None:
        cache.set(_activity_key(user.pk), now, ACTIVITY_TTL)
        return
    last_login = user.last_login.timestamp() if user.last_login else None
    reference = max(t for t in (last_activity, last_login) if t is not None)
    if now - reference > minutes * 60:
        raise AuthenticationFailed({
            'detail': f'You were signed out after {minutes} minutes of inactivity. Sign in again to continue.',
            'code': IDLE_CODE,
        })
    if not background and (last_activity is None or now - last_activity >= ACTIVITY_WRITE_EVERY):
        cache.set(_activity_key(user.pk), now, ACTIVITY_TTL)


# ---------------------------------------------------------------- allowed IP ranges
def ip_in_ranges(ip, ranges):
    try:
        address = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    for cidr in ranges:
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def check_ip(user, request):
    """Raise AuthenticationFailed when the request comes from outside the organization's IP ranges.

    Superusers are exempt so a platform administrator can always get in and fix a bad list.
    """
    ranges = (active_policy(user) or {}).get('allowed_ip_ranges') or []
    if not ranges or user.is_superuser:
        return
    from .audit import client_ip
    ip = client_ip(request)
    if not ip_in_ranges(ip, ranges):
        raise AuthenticationFailed({
            'detail': "Your network isn't on your organization's allowed list. Connect from an approved "
                      'network (for example the office or VPN) and sign in again.',
            'code': IP_CODE,
        })


class PolicyJWTAuthentication(JWTAuthentication):
    """SimpleJWT authentication plus the organization's IP ranges and session idle timeout."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            check_ip(result[0], request)
            check_idle(result[0], background=request.META.get(BACKGROUND_HEADER) == '1')
        return result
