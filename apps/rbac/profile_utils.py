"""
Profile Provisioning Utilities — Soft-Coded, Single Source of Truth
====================================================================
Centralizes the "get or auto-create UserProfile for the current user" logic
that Achievements / Experience / Social Links / Documents all need.

Previously this ~30-line block was copy-pasted into four separate ViewSets,
each one drifting slightly out of sync with the others (e.g. one had the
'organization' default, another didn't) — that drift is exactly what caused
the "user_profile: This field is required" production bug. Fix it once here.
"""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)


class ProfileProvisioningError(Exception):
    """Raised when a UserProfile genuinely cannot be created (not a bug — e.g.
    no Organization exists yet). Caught by the ViewSet and turned into a clean
    400 response instead of a raw 500 traceback."""


def get_or_create_profile(user, *, source: str = 'unknown'):
    """
    Return the user's UserProfile, auto-provisioning one on first use.

    Soft-coded via PROFILE_AUTO_PROVISION in profile_config.py — change the
    default organization there, not here.

    Args:
        user: the authenticated Django User.
        source: short label (e.g. 'AchievementViewSet') used only for logging.

    Returns:
        UserProfile instance.

    Raises:
        ProfileProvisioningError: if provisioning fails for a genuine reason.
    """
    # Fast path — already has a profile (the overwhelming majority of calls).
    profile = getattr(user, 'rbac_profile', None)
    if profile is not None:
        return profile

    from .models import Organization, UserProfile
    from .profile_config import PROFILE_AUTO_PROVISION

    logger.info(f'[ProfileProvisioning] No UserProfile for user {user.id} ({user.email}) — auto-creating (source={source})')

    try:
        with transaction.atomic():
            organization = Organization.objects.filter(is_active=True).first()
            if organization is None:
                organization, org_created = Organization.objects.get_or_create(
                    code=PROFILE_AUTO_PROVISION['organization_code'],
                    defaults={
                        'name': PROFILE_AUTO_PROVISION['organization_name'],
                        'description': PROFILE_AUTO_PROVISION['organization_description'],
                        'is_active': True,
                    },
                )
                if org_created:
                    logger.warning(f'[ProfileProvisioning] Auto-created default organization "{organization.name}"')

            profile, created = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'organization': organization,
                    'bio': '',
                    'job_title': user.username or (user.email or '').split('@')[0],
                },
            )
    except Exception as exc:
        # Any downstream signal (e.g. default-role assignment, cache clearing)
        # raising should never surface as a confusing "field is required"
        # error — wrap it in one clear, actionable message instead.
        logger.exception(f'[ProfileProvisioning] Failed to provision profile for user {user.id} (source={source})')
        raise ProfileProvisioningError(
            'Could not set up your profile automatically. Please refresh and try again, '
            'or contact an administrator if this keeps happening.'
        ) from exc

    if created:
        logger.info(f'[ProfileProvisioning] ✅ Created UserProfile {profile.id} for user {user.id} (source={source})')

    return profile
