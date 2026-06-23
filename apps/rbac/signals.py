"""
RBAC Signals
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.core.cache import cache
from .models import UserProfile, Organization, UserRole
from .utils import create_audit_log

User = get_user_model()


@receiver(post_save, sender=User)
def log_user_login(sender, instance, created, **kwargs):
    """
    Log user login events
    """
    if not created and instance.last_login:
        try:
            profile = instance.rbac_profile
            profile.last_login_at = instance.last_login
            profile.failed_login_attempts = 0  # Reset on successful login
            profile.save(update_fields=['last_login_at', 'failed_login_attempts'])
        except UserProfile.DoesNotExist:
            pass


@receiver(post_save, sender=UserRole)
@receiver(post_delete, sender=UserRole)
def clear_user_permissions_cache(sender, instance, **kwargs):
    """
    Clear user permissions and modules cache when roles are assigned/removed
    """
    profile_id = instance.user_profile_id
    cache.delete(f'user_permissions_{profile_id}')
    cache.delete(f'user_modules_{profile_id}')
    print(f"[Cache] Cleared permissions and modules cache for user {profile_id}")


# Soft-coded: default role assigned to every new user profile
DEFAULT_ROLE_CODE = 'viewer'


@receiver(post_save, sender=UserProfile)
def assign_default_role_on_profile_creation(sender, instance, created, **kwargs):
    """
    Auto-assign the default viewer role to every new UserProfile so that
    all users have baseline access (Dashboard, Engineering, Common, HR Self-Service)
    without requiring manual intervention.
    """
    if not created:
        return
    from .models import Role, UserRole  # local import to avoid circular
    try:
        viewer_role = Role.objects.get(code=DEFAULT_ROLE_CODE, is_active=True)
        UserRole.objects.get_or_create(
            user_profile=instance,
            role=viewer_role,
            defaults={'is_primary': True},
        )
    except Role.DoesNotExist:
        # Viewer role not yet seeded (e.g., fresh migrations) — skip silently
        pass
