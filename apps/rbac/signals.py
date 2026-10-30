"""
RBAC Signals
"""

import logging
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.core.cache import cache
from .models import UserProfile, Organization, UserRole, RoleModule, RolePermission, Delegation, Role, Module, Permission
from .utils import create_audit_log
logger = logging.getLogger(__name__)

User = get_user_model()

# Track previous profile status across pre_save/post_save boundary
_profile_previous_status = {}



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
        logger.debug(f"[Cache] Cleared permissions and modules cache for user {profile_id}")
    except Exception as exc:
        logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")


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
            logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
    
    if cleared_count > 0:
        logger.debug(f"[Cache] Cleared cache for {cleared_count} user(s) affected by role {role_id} module change")


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
            logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")

    if cleared_count > 0:
        logger.debug(f"[Cache] Cleared permissions cache for {cleared_count} user(s) affected by role {role_id} permission change")


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
        logger.debug(f"[RBAC] WARNING Failed to assign default role to profile {instance.id}: {exc}")


@receiver(post_save, sender='rbac.Module')
def ensure_saved_module_actions(sender, instance, using, raw=False, **kwargs):
    """New or re-enabled modules receive the same editable action catalogue."""
    if raw or not instance.is_active:
        return
    from .models import Permission
    from .module_actions import ensure_module_actions
    from django.db import transaction
    transaction.on_commit(lambda: ensure_module_actions(sender, Permission, using=using, module_ids=[instance.pk]), using=using)


@receiver(pre_save, sender=UserProfile)
def capture_profile_previous_status(sender, instance, **kwargs):
    """Capture previous status/is_deleted before save for cache invalidation."""
    if instance.pk:
        try:
            prev = UserProfile.objects.only('status', 'is_deleted').get(pk=instance.pk)
            _profile_previous_status[instance.pk] = (prev.status, prev.is_deleted)
        except UserProfile.DoesNotExist:
            pass


@receiver(post_save, sender=UserProfile)
def clear_profile_status_cache(sender, instance, created, **kwargs):
    """
    Clear user cache when profile status changes (active/suspended/deleted).
    A suspended or deleted user must immediately lose cached access.
    """
    if created:
        return
    prev = _profile_previous_status.pop(instance.pk, None)
    if prev and (prev[0] != instance.status or prev[1] != instance.is_deleted):
        try:
            cache.delete(f'user_permissions_{instance.id}')
            cache.delete(f'user_modules_{instance.id}')
            logger.debug(f"[Cache] Cleared cache for user {instance.id} (status={instance.status}, deleted={instance.is_deleted})")
        except Exception as exc:
            logger.debug(f"[Cache] WARNING Failed to clear cache for user {instance.id}: {exc}")


@receiver(post_save, sender=Role)
@receiver(post_delete, sender=Role)
def clear_role_status_cache(sender, instance, **kwargs):
    """
    Clear module/permission cache for ALL users of a role when role is_active changes.
    """
    role_id = instance.id
    try:
        profile_ids = UserRole.objects.filter(
            role_id=role_id
        ).values_list('user_profile_id', flat=True)
        cleared_count = 0
        for profile_id in profile_ids:
            try:
                cache.delete(f'user_permissions_{profile_id}')
                cache.delete(f'user_modules_{profile_id}')
                cleared_count += 1
            except Exception as exc:
                logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
        if cleared_count > 0:
            logger.debug(f"[Cache] Cleared cache for {cleared_count} user(s) affected by role {role_id} status change")
    except Exception as exc:
        logger.debug(f"[Cache] WARNING Failed to clear role cache for role {role_id}: {exc}")


@receiver(post_save, sender=Module)
@receiver(post_delete, sender=Module)
def clear_module_status_cache(sender, instance, **kwargs):
    """
    Clear cache for all users when a module is_active changes.
    """
    module_id = instance.id
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        profile_ids = UserProfile.objects.filter(
            userrole__role__rolemodule__module_id=module_id
        ).values_list('id', flat=True).distinct()
        cleared_count = 0
        for profile_id in profile_ids:
            try:
                cache.delete(f'user_permissions_{profile_id}')
                cache.delete(f'user_modules_{profile_id}')
                cleared_count += 1
            except Exception as exc:
                logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
        if cleared_count > 0:
            logger.debug(f"[Cache] Cleared cache for {cleared_count} user(s) affected by module {module_id} status change")
    except Exception as exc:
        logger.debug(f"[Cache] WARNING Failed to clear module cache for module {module_id}: {exc}")


@receiver(post_save, sender=Permission)
@receiver(post_delete, sender=Permission)
def clear_permission_status_cache(sender, instance, **kwargs):
    """
    Clear cache for all users when a permission is_active changes.
    """
    permission_id = instance.id
    module_id = instance.module_id
    try:
        profile_ids = UserProfile.objects.filter(
            userrole__role__rolepermission__permission_id=permission_id
        ).values_list('id', flat=True).distinct()
        # Also clear users with module-level access via RoleModule
        profile_ids = list(profile_ids) + list(
            UserProfile.objects.filter(
                userrole__role__rolemodule__module_id=module_id
            ).values_list('id', flat=True).distinct()
        )
        cleared_count = 0
        for profile_id in set(profile_ids):
            try:
                cache.delete(f'user_permissions_{profile_id}')
                cache.delete(f'user_modules_{profile_id}')
                cleared_count += 1
            except Exception as exc:
                logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
        if cleared_count > 0:
            logger.debug(f"[Cache] Cleared cache for {cleared_count} user(s) affected by permission {permission_id} status change")
    except Exception as exc:
        logger.debug(f"[Cache] WARNING Failed to clear permission cache for permission {permission_id}: {exc}")


@receiver(post_save, sender=Delegation)
@receiver(post_delete, sender=Delegation)
def clear_delegation_cache(sender, instance, **kwargs):
    """
    Clear cache for both delegator and delegatee when a delegation is created/removed.
    """
    for profile_id in (instance.delegator_id, instance.delegatee_id):
        try:
            cache.delete(f'user_permissions_{profile_id}')
            cache.delete(f'user_modules_{profile_id}')
        except Exception as exc:
            logger.debug(f"[Cache] WARNING Failed to clear cache for user {profile_id}: {exc}")
