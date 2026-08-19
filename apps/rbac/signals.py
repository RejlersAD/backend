"""
RBAC Signals
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.core.cache import cache
from .models import UserProfile, Organization, UserRole, RoleModule, RolePermission
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
    Clear user permissions and modules cache when roles are assigned/removed.

    Cache invalidation is best-effort: a Redis hiccup here must never roll
    back the UserRole save/delete that triggered it (that previously broke
    UserProfile auto-creation whenever Redis had a transient auth/connection
    error — see profile_utils.get_or_create_profile).
    """
    profile_id = instance.user_profile_id
    try:
        cache.delete(f'user_permissions_{profile_id}')
        cache.delete(f'user_modules_{profile_id}')
        print(f"[Cache] Cleared permissions and modules cache for user {profile_id}")
    except Exception as exc:
        print(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")


@receiver(post_save, sender=RoleModule)
@receiver(post_delete, sender=RoleModule)
def clear_role_users_cache(sender, instance, **kwargs):
    """
    Clear module cache for ALL users who have this role when modules are added/removed from the role
    CRITICAL: Fixes issue where custom role module changes don't reflect until cache expires
    """
    role_id = instance.role_id
    
    # Get all user profiles that have this role
    profile_ids = UserRole.objects.filter(
        role_id=role_id,
        role__is_active=True
    ).values_list('user_profile_id', flat=True)
    
    # Clear cache for each affected user (best-effort — see comment above)
    cleared_count = 0
    for profile_id in profile_ids:
        try:
            cache.delete(f'user_permissions_{profile_id}')
            cache.delete(f'user_modules_{profile_id}')
            cleared_count += 1
        except Exception as exc:
            print(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
    
    if cleared_count > 0:
        print(f"[Cache] Cleared cache for {cleared_count} user(s) affected by role {role_id} module change")


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
def clear_role_permission_users_cache(sender, instance, **kwargs):
    """
    Clear permissions cache for ALL users who have this role when permissions
    are added/removed from the role (mirrors clear_role_users_cache for modules).
    """
    role_id = instance.role_id

    profile_ids = UserRole.objects.filter(
        role_id=role_id,
        role__is_active=True
    ).values_list('user_profile_id', flat=True)

    cleared_count = 0
    for profile_id in profile_ids:
        try:
            cache.delete(f'user_permissions_{profile_id}')
            cleared_count += 1
        except Exception as exc:
            print(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")

    if cleared_count > 0:
        print(f"[Cache] Cleared permissions cache for {cleared_count} user(s) affected by role {role_id} permission change")


# Soft-coded: read the default role code from rbac_config so a single config
# change flips the baseline role for the whole system. Was previously hardcoded
# to 'viewer' — now points to the Default role defined in DEFAULT_ROLE_CONFIG.
def _get_default_role_code():
    from .rbac_config import DEFAULT_ROLE_CONFIG
    return DEFAULT_ROLE_CONFIG.get('code', 'default')


@receiver(post_save, sender=UserProfile)
def assign_default_role_on_profile_creation(sender, instance, created, **kwargs):
    """
    Auto-assign the system default role to every new UserProfile so that all
    users have baseline access (engineering modules, common tools, HR
    self-service) without manual intervention.

    Super Administrators (Django is_superuser=True) are excluded — they
    bypass every module check and do not need the Default role.

    The role code is soft-coded via DEFAULT_ROLE_CONFIG in rbac_config.py.
    """
    if not created:
        return

    # Skip Super Administrators — they already bypass all access checks.
    if getattr(instance.user, 'is_superuser', False):
        return

    from .models import Role, UserRole  # local import to avoid circular
    default_code = _get_default_role_code()
    try:
        default_role = Role.objects.get(code=default_code, is_active=True)
        UserRole.objects.get_or_create(
            user_profile=instance,
            role=default_role,
            defaults={'is_primary': True},
        )
    except Role.DoesNotExist:
        # Default role not yet seeded (e.g. fresh migrations) — skip silently.
        pass
    except Exception as exc:
        # Never let default-role assignment block profile creation itself —
        # the profile has already been saved by this point.
        print(f"[RBAC] WARNING Failed to assign default role to profile {instance.id}: {exc}")
