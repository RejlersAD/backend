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
        # ROBUST: Ensure organization exists OUTSIDE the transaction to avoid deadlocks
        logger.info(f'[ProfileProvisioning] Step 1: Getting or creating organization...')
        
        organization = Organization.objects.filter(is_active=True).first()
        
        if organization is None:
            logger.warning(f'[ProfileProvisioning] No active organization found. Creating default...')
            
            # Try multiple times with different codes to handle race conditions
            for attempt in range(3):
                try:
                    organization, org_created = Organization.objects.get_or_create(
                        code=PROFILE_AUTO_PROVISION['organization_code'],
                        defaults={
                            'name': PROFILE_AUTO_PROVISION['organization_name'],
                            'description': PROFILE_AUTO_PROVISION['organization_description'],
                            'is_active': True,
                        },
                    )
                    if org_created:
                        logger.info(f'[ProfileProvisioning] ✅ Created default organization: {organization.name} (ID: {organization.id})')
                    else:
                        logger.info(f'[ProfileProvisioning] ✅ Using existing organization: {organization.name} (ID: {organization.id})')
                    break
                except Exception as org_exc:
                    if attempt == 2:  # Last attempt
                        logger.error(f'[ProfileProvisioning] Failed to create organization after 3 attempts: {org_exc}')
                        raise
                    logger.warning(f'[ProfileProvisioning] Organization creation attempt {attempt + 1} failed, retrying...')
                    # Try to get any organization as fallback
                    organization = Organization.objects.first()
                    if organization:
                        logger.info(f'[ProfileProvisioning] Using fallback organization: {organization.name}')
                        break
        else:
            logger.info(f'[ProfileProvisioning] ✅ Using organization: {organization.name} (ID: {organization.id})')
        
        if not organization:
            raise ProfileProvisioningError('No organization available. Please contact administrator.')
        
        # ROBUST: Create profile with explicit error handling
        logger.info(f'[ProfileProvisioning] Step 2: Creating UserProfile for user {user.id}...')
        
        with transaction.atomic():
            profile, created = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'organization': organization,
                    'bio': '',
                    'job_title': user.username or (user.email or '').split('@')[0] if user.email else 'User',
                },
            )
            
            if created:
                logger.info(f'[ProfileProvisioning] ✅ Created UserProfile {profile.id} for user {user.id} ({user.email})')
            else:
                logger.info(f'[ProfileProvisioning] ℹ️  UserProfile {profile.id} already exists for user {user.id}')
        
        # Final verification
        if not profile:
            raise ProfileProvisioningError('Profile creation returned None')
        
        logger.info(f'[ProfileProvisioning] ✅ SUCCESS: Profile {profile.id} ready for user {user.id} (source={source})')
        return profile
        
    except ProfileProvisioningError:
        # Re-raise our own errors as-is
        raise
    except Exception as exc:
        # Any downstream signal (e.g. default-role assignment, cache clearing)
        # raising should never surface as a confusing "field is required"
        # error — wrap it in one clear, actionable message instead.
        logger.exception(f'[ProfileProvisioning] ❌ FAILED to provision profile for user {user.id} (source={source})')
        logger.error(f'[ProfileProvisioning] Exception type: {type(exc).__name__}')
        logger.error(f'[ProfileProvisioning] Exception message: {str(exc)}')
        
        # Provide specific error messages based on exception type
        error_msg = 'Could not set up your profile automatically. '
        
        if 'organization' in str(exc).lower():
            error_msg += 'Organization setup failed. '
        elif 'database' in str(exc).lower() or 'connection' in str(exc).lower():
            error_msg += 'Database connection issue. '
        elif 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            error_msg += 'Profile may already exist. '
        
        error_msg += 'Please refresh and try again, or contact an administrator if this keeps happening.'
        
        raise ProfileProvisioningError(error_msg) from exc
